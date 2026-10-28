import logging
import threading

import requests

logger = logging.getLogger(__name__)


def _send_push(push_token, title, body, ref_no):
    try:
        response = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json={
                "to": push_token,
                "title": title,
                "body": body,
                "data": {"ref_no": ref_no},
                "sound": "default",
            },
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Expo push delivery failed for report %s", ref_no)


def notify_reporter(report, message):
    token = report.reporter.expo_push_token
    if token:
        thread = threading.Thread(
            target=_send_push,
            args=(token, f"Report {report.ref_no}", message, report.ref_no),
            daemon=True,
        )
        thread.start()


def notify_emergency_requester(emergency, message):
    if not emergency.requester_id:
        return
    token = emergency.requester.expo_push_token
    if token:
        thread = threading.Thread(
            target=_send_push,
            args=(token, f"Request {emergency.ref_no}", message, emergency.ref_no),
            daemon=True,
        )
        thread.start()
