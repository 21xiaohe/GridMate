import pytest

from app.core import selfcheck


@pytest.mark.asyncio
async def test_check_sufficient_parses_flat(monkeypatch):
    class Fake:
        useful = False
        reason = "证据只讲违约金,未涉及型号"

    async def fake_invoke(_):
        return Fake()

    class Chain:
        def __or__(self, o):
            return self

        async def ainvoke(self, _):
            return Fake()

    monkeypatch.setattr("app.core.selfcheck._chain", lambda: Chain())
    out = await selfcheck.check_sufficient("Pro型号功能", ["满99缴费"])
    assert out == {"useful": False, "reason": "证据只讲违约金,未涉及型号"}
