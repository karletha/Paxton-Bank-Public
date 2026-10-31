"""Minimal interface stub for optional Zoom RTMS connector (omitted in public reference build)."""
CREDS = {}


class ZoomWatcher:
    def __init__(self, rooms):
        self.rooms = rooms

    async def run(self):
        return


def handle_webhook(body: dict) -> dict:
    return {"status": "stub"}


async def get_user_token() -> str:
    return ""


def sdk_signature(meeting_number: str, role: int) -> str:
    return ""
