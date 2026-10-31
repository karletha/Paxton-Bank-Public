"""Minimal interface stub for optional external media relay (omitted in public reference build)."""
from fastapi import APIRouter

router = APIRouter()
hopframe = {}


class _StubChannel:
    ring = []
    ring_bytes = 0
    subs = set()

    async def publish(self, data: bytes):
        return


def get_channel(room: str, who: str) -> _StubChannel:
    return _StubChannel()
