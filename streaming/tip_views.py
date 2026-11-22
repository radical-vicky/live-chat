import hmac
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import MpesaTipForm
from .models import LiveSession, MpesaTip, SessionParticipant, UploadedVideo
from .mpesa import (
    MpesaConfigurationError,
    MpesaError,
    MpesaNetworkError,
    initiate_stk_push,
    is_mpesa_configured,
    query_stk_push,
    user_display_name,
)


logger = logging.getLogger(__name__)


def _tip_status_url(tip):
    return reverse("mpesa_tip_status", kwargs={"tip_id": tip.public_id})


def _json_form_error(form):
    return JsonResponse(
        {"error": "Check the amount and Kenyan M-Pesa phone number.", "fields": form.errors.get_json_data()},
        status=400,
    )


@login_required
@require_POST
def create_mpesa_tip(request):
    form = MpesaTipForm(request.POST)
    if not form.is_valid():
        return _json_form_error(form)
    if not is_mpesa_configured():
        return JsonResponse(
            {"error": "M-Pesa tipping is not configured on this server."},
            status=503,
        )

    target_type = form.cleaned_data["target_type"]
    target_id = form.cleaned_data["target_id"]
    if target_type == "video":
        target = (
            UploadedVideo.objects.select_related("owner")
            .filter(pk=target_id)
            .first()
        )
        if target is None:
            return JsonResponse({"error": "Video not found."}, status=404)
        recipient = target.owner
        target_title = target.title
    else:
        target = (
            LiveSession.objects.select_related("host")
            .filter(pk=target_id)
            .first()
        )
        if target is None:
            return JsonResponse({"error": "Live session not found."}, status=404)
        if target.status != "live":
            return JsonResponse(
                {"error": "You can only tip a live session while it is live."},
                status=409,
            )
        if (
            target.visibility == "private"
            and target.host_id != request.user.pk
            and not SessionParticipant.objects.filter(
                session=target,
                user=request.user,
                left_at__isnull=True,
            ).exists()
        ):
            return JsonResponse(
                {"error": "You cannot tip this private session."},
                status=403,
            )
        recipient = target.host
        target_title = target.title

    if recipient.pk == request.user.pk:
        return JsonResponse(
            {"error": "You cannot tip your own content."},
            status=400,
        )

    tip = MpesaTip.objects.create(
        sender=request.user,
        recipient=recipient,
        recipient_name=user_display_name(recipient),
        target_type=target_type,
        target_id=target_id,
        target_title=target_title,
        amount=form.cleaned_data["amount"],
    )
    try:
        checkout = initiate_stk_push(
            tip,
            form.cleaned_data["phone_number"],
        )
    except MpesaNetworkError:
        logger.warning(
            "M-Pesa initiation response was uncertain for tip=%s",
            tip.public_id,
        )
        return JsonResponse(
            {
                "tip_id": str(tip.public_id),
                "status": tip.status,
                "status_url": _tip_status_url(tip),
                "message": (
                    "We could not confirm the request response. "
                    "Wait for the M-Pesa prompt or contact support before retrying."
                ),
            },
            status=202,
        )
    except MpesaConfigurationError:
        tip.status = "failed"
        tip.failure_reason = "M-Pesa configuration is incomplete."
        tip.save(update_fields=["status", "failure_reason"])
        return JsonResponse(
            {"error": "M-Pesa tipping is not configured correctly."},
            status=503,
        )
    except MpesaError:
        logger.exception("M-Pesa rejected tip initiation for tip=%s", tip.public_id)
        tip.status = "failed"
        tip.failure_reason = "M-Pesa could not start the payment."
        tip.save(update_fields=["status", "failure_reason"])
        return JsonResponse(
            {"error": "M-Pesa could not start the payment. Please try again."},
            status=502,
        )

    tip.merchant_request_id = checkout["merchant_request_id"]
    tip.checkout_request_id = checkout["checkout_request_id"]
    tip.save(update_fields=["merchant_request_id", "checkout_request_id"])
    return JsonResponse({
        "tip_id": str(tip.public_id),
        "status": tip.status,
        "status_url": _tip_status_url(tip),
        "message": (
            checkout["customer_message"]
            or "Check your phone and enter your M-Pesa PIN to complete the tip."
        ),
    })


@login_required
@require_GET
def mpesa_tip_status(request, tip_id):
    tip = get_object_or_404(
        MpesaTip,
        public_id=tip_id,
        sender=request.user,
    )
    return JsonResponse({
        "status": tip.status,
        "receipt": tip.mpesa_receipt_number or "",
        "message": tip.failure_reason,
    })


def _callback_ack():
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


def _callback_error(message, status):
    return JsonResponse(
        {"ResultCode": 1, "ResultDesc": message},
        status=status,
    )


def _callback_metadata(callback):
    metadata = callback.get("CallbackMetadata", {})
    items = metadata.get("Item", []) if isinstance(metadata, dict) else []
    if not isinstance(items, list):
        return {}
    return {
        item.get("Name"): item.get("Value")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("Name"), str)
    }


@csrf_exempt
@require_POST
def mpesa_tip_callback(request, token):
    expected_token = settings.MPESA_CALLBACK_TOKEN
    if not expected_token or not hmac.compare_digest(token, expected_token):
        return _callback_error("Not found.", 404)
    if len(request.body) > 65536:
        return _callback_error("Callback is too large.", 413)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _callback_error("Invalid callback body.", 400)
    callback = (
        payload.get("Body", {}).get("stkCallback")
        if isinstance(payload, dict)
        and isinstance(payload.get("Body"), dict)
        else None
    )
    if not isinstance(callback, dict):
        return _callback_error("Invalid callback data.", 400)

    checkout_request_id = callback.get("CheckoutRequestID")
    merchant_request_id = callback.get("MerchantRequestID")
    if not checkout_request_id or not merchant_request_id:
        return _callback_error("Missing checkout identifiers.", 400)
    tip = MpesaTip.objects.filter(
        checkout_request_id=str(checkout_request_id),
        merchant_request_id=str(merchant_request_id),
    ).first()
    if tip is None:
        logger.warning(
            "Received unmatched M-Pesa callback checkout=%s",
            checkout_request_id,
        )
        return _callback_error("Unknown checkout.", 404)
    if tip.status == "succeeded":
        return _callback_ack()

    result_code = callback.get("ResultCode")
    if str(result_code) != "0":
        with transaction.atomic():
            locked_tip = MpesaTip.objects.select_for_update().get(pk=tip.pk)
            if locked_tip.status != "succeeded":
                locked_tip.status = "failed"
                locked_tip.failure_reason = str(
                    callback.get("ResultDesc", "M-Pesa payment failed.")
                )[:255]
                locked_tip.save(update_fields=["status", "failure_reason"])
        return _callback_ack()

    metadata = _callback_metadata(callback)
    try:
        paid_amount = int(metadata.get("Amount"))
    except (TypeError, ValueError):
        return _callback_error("Missing payment amount.", 400)
    receipt = metadata.get("MpesaReceiptNumber")
    if paid_amount != tip.amount or not isinstance(receipt, str) or not receipt:
        logger.warning(
            "M-Pesa callback data did not match tip=%s",
            tip.public_id,
        )
        return _callback_error("Payment details did not match.", 400)

    try:
        query = query_stk_push(str(checkout_request_id))
    except MpesaError:
        logger.exception(
            "Could not verify M-Pesa payment for tip=%s",
            tip.public_id,
        )
        return _callback_error("Payment verification is unavailable.", 503)
    if str(query.get("ResultCode")) != "0":
        logger.warning(
            "M-Pesa query did not confirm tip=%s",
            tip.public_id,
        )
        return _callback_error("Payment is not verified yet.", 503)

    try:
        with transaction.atomic():
            locked_tip = MpesaTip.objects.select_for_update().get(pk=tip.pk)
            if locked_tip.status != "succeeded":
                locked_tip.status = "succeeded"
                locked_tip.payout_status = "unpaid"
                locked_tip.mpesa_receipt_number = receipt[:30]
                locked_tip.failure_reason = ""
                locked_tip.paid_at = timezone.now()
                locked_tip.save(update_fields=[
                    "status",
                    "payout_status",
                    "mpesa_receipt_number",
                    "failure_reason",
                    "paid_at",
                ])
    except IntegrityError:
        logger.exception(
            "Duplicate M-Pesa receipt for tip=%s",
            tip.public_id,
        )
        return _callback_error("Receipt was already recorded.", 409)

    return _callback_ack()
