# RADEV LIVE

## Run locally

Use the project's virtual environment from the repository root:

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver
```

The development server uses Daphne/ASGI so the room chat, direct messages, participant updates, and notifications can use WebSockets.

## Real-time services

The application reads environment values from `.env` in the repository root (without overriding values already set in the process):

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

These LiveKit values are required for live audio/video and screen sharing. If they are missing or invalid, the room displays the token error rather than simulating a connection.

Hosts can mute active participants through LiveKit, disable room chat, and lock the room to new participants. Chat and lock settings are stored with the session; participant microphone state is synchronized with the room controls.

## Live replay previews

Live sessions are recorded as full-room MP4 replays through LiveKit Egress. The host must accept the recording notice before starting, and every participant must accept it before joining. Recording begins when the host enters the room and is finalized when the host ends the session. Public replays appear in Explore and creator profiles; viewers can play, like, comment, save, and follow the host. Owners can edit the replay title, description, and thumbnail or delete the replay.

Recording requires LiveKit Egress (available with LiveKit Cloud, or deploy Egress alongside a self-hosted LiveKit server) and an S3-compatible bucket accessible to both the Egress service and this Django app. Configure these environment values in `.env`:

- `LIVEKIT_RECORDING_S3_BUCKET`
- `LIVEKIT_RECORDING_S3_REGION`
- `LIVEKIT_RECORDING_S3_ACCESS_KEY`
- `LIVEKIT_RECORDING_S3_SECRET`
- `LIVEKIT_RECORDING_S3_ENDPOINT` (optional; for S3-compatible providers)
- `LIVEKIT_RECORDING_S3_FORCE_PATH_STYLE` (optional; set to `true` for providers that require path-style URLs)
- `LIVEKIT_RECORDING_S3_PUBLIC_BASE_URL` (optional; use a public bucket/CDN base URL instead of signed playback URLs)

Use restricted storage credentials that allow Egress to upload replay objects and the app to read/check/delete them. Without recording and storage configuration, the app will not start a live session, rather than silently skipping the promised replay. Private sessions remain private.

`REDIS_URL` is optional for local, single-process development. Without it, Channels uses its in-memory layer. Set `REDIS_URL` to a reachable Redis instance when running multiple application workers so WebSocket events are shared across processes.

## M-Pesa tips

Viewers can send KES tips to video creators and live-session hosts using Daraja STK Push. The payment is collected into the platform's configured M-Pesa shortcode; creator payouts are manual and can be tracked from the Django admin. The payer's phone number is sent to Daraja for checkout but is not stored in the tip ledger.

Configure these values in `.env` using your Daraja app and shortcode details:

- `MPESA_ENVIRONMENT=sandbox` or `live`
- `MPESA_CONSUMER_KEY`
- `MPESA_CONSUMER_SECRET`
- `MPESA_SHORTCODE`
- `MPESA_PASSKEY`
- `MPESA_TRANSACTION_TYPE=CustomerPayBillOnline` (or `CustomerBuyGoodsOnline` for a Till)
- `MPESA_CALLBACK_BASE_URL` (the public HTTPS origin of this app)
- `MPESA_CALLBACK_TOKEN` (a long random URL-safe token; never commit or share it)

Daraja must be able to reach the generated callback at `/payments/mpesa/callback/<token>/`. Local development therefore needs a public HTTPS tunnel configured as the callback base URL. Callback success is checked against Daraja's STK Push Query API before a tip is recorded as paid. Do not mark a tip as paid to its creator until the manual transfer has actually been sent.

Optional amount settings are `MPESA_TIP_MIN_AMOUNT` (default `10`), `MPESA_TIP_MAX_AMOUNT` (default `150000`), and comma-separated `MPESA_TIP_PRESETS` (default `50,100,500`). Amounts are whole KES. Sandbox transactions do not move real funds; switch to `live` only after Daraja has approved the production app and shortcode.

## Checks

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py test
```
