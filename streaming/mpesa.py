import base64
import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils import timezone


class MpesaError(Exception):
    pass


class MpesaNetworkError(MpesaError):
    pass


class MpesaConfigurationError(MpesaError):
    pass


def is_mpesa_configured():
    configured = all((
        settings.MPESA_API_BASE_URL,
        settings.MPESA_CONSUMER_KEY,
        settings.MPESA_CONSUMER_SECRET,
        settings.MPESA_SHORTCODE,
        settings.MPESA_PASSKEY,
        settings.MPESA_CALLBACK_BASE_URL,
        settings.MPESA_CALLBACK_TOKEN,
    ))
    callback = urlparse(settings.MPESA_CALLBACK_BASE_URL)
    return configured and callback.scheme == "https" and bool(callback.netloc)


def tip_template_context():
    return {
        "mpesa_configured": is_mpesa_configured(),
        "tip_min_amount": settings.MPESA_TIP_MIN_AMOUNT,
        "tip_max_amount": settings.MPESA_TIP_MAX_AMOUNT,
        "tip_presets": settings.MPESA_TIP_PRESETS,
    }


def user_display_name(user):
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        profile = None
    return (
        (profile.full_name if profile else "")
        or user.get_full_name()
        or user.get_username()
    )


def normalize_phone_number(value):
    phone = re.sub(r"[\s()-]", "", value)
    if phone.startswith("+254"):
        phone = phone[1:]
    elif phone.startswith("0"):
        phone = "254" + phone[1:]
    if not re.fullmatch(r"254[17]\d{8}", phone):
        raise ValueError("Enter a valid Kenyan M-Pesa phone number.")
    return phone


def _ensure_configured():
    if not is_mpesa_configured():
        raise MpesaConfigurationError(
            "M-Pesa tipping is not configured on this server."
        )
    callback = urlparse(settings.MPESA_CALLBACK_BASE_URL)
    if callback.scheme != "https" or not callback.netloc:
        raise MpesaConfigurationError(
            "M-Pesa callbacks require a public HTTPS base URL."
        )


def _timestamp():
    east_africa_time = datetime.now(datetime_timezone.utc).astimezone(
        datetime_timezone(timedelta(hours=3)),
    )
    return east_africa_time.strftime("%Y%m%d%H%M%S")


def _password(timestamp):
    value = (
        f"{settings.MPESA_SHORTCODE}"
        f"{settings.MPESA_PASSKEY}"
        f"{timestamp}"
    )
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _response_json(response):
    try:
        result = response.json()
    except ValueError as exc:
        raise MpesaError("M-Pesa returned an invalid response.") from exc
    if not response.ok:
        raise MpesaError("M-Pesa could not process the request.")
    if not isinstance(result, dict):
        raise MpesaError("M-Pesa returned an invalid response.")
    return result


def _access_token():
    _ensure_configured()
    try:
        response = requests.get(
            f"{settings.MPESA_API_BASE_URL}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(
                settings.MPESA_CONSUMER_KEY,
                settings.MPESA_CONSUMER_SECRET,
            ),
            timeout=(5, 20),
        )
    except requests.RequestException as exc:
        raise MpesaNetworkError(
            "Could not connect to M-Pesa."
        ) from exc

    result = _response_json(response)
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise MpesaError("M-Pesa did not provide an access token.")
    return token


def _post_request(endpoint, payload):
    token = _access_token()
    try:
        response = requests.post(
            f"{settings.MPESA_API_BASE_URL}{endpoint}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=(5, 30),
        )
    except requests.RequestException as exc:
        raise MpesaNetworkError(
            "Could not confirm the M-Pesa request response."
        ) from exc
    return _response_json(response)


def initiate_stk_push(tip, phone_number):
    timestamp = _timestamp()
    callback_url = (
        settings.MPESA_CALLBACK_BASE_URL
        + reverse(
            "mpesa_tip_callback",
            kwargs={"token": settings.MPESA_CALLBACK_TOKEN},
        )
    )
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": settings.MPESA_TRANSACTION_TYPE,
        "Amount": tip.amount,
        "PartyA": phone_number,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone_number,
        "CallBackURL": callback_url,
        "AccountReference": f"TIP{tip.public_id.hex[:9]}",
        "TransactionDesc": "Creator tip",
    }
    result = _post_request(
        "/mpesa/stkpush/v1/processrequest",
        payload,
    )
    if str(result.get("ResponseCode")) != "0":
        raise MpesaError("M-Pesa did not accept the STK Push request.")
    merchant_request_id = result.get("MerchantRequestID")
    checkout_request_id = result.get("CheckoutRequestID")
    if not merchant_request_id or not checkout_request_id:
        raise MpesaError("M-Pesa returned incomplete checkout details.")
    return {
        "merchant_request_id": str(merchant_request_id),
        "checkout_request_id": str(checkout_request_id),
        "customer_message": str(result.get("CustomerMessage", ""))[:255],
    }


def query_stk_push(checkout_request_id):
    timestamp = _timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }
    return _post_request(
        "/mpesa/stkpushquery/v1/query",
        payload,
    )
