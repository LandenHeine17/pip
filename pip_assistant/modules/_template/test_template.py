"""Every module has its own tests. This one loads the template like pip-core would."""
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry


def test_the_template_module_works():
    info = read(ROOT / "_template")
    reg = Registry.load({}, infos=[info])
    act = reg.action("roll")
    plan = act.check({"do": "roll", "sides": 6}, Call())
    assert act.do(plan, Call()) in {f"It's a {n}." for n in range(1, 7)}
    with pytest.raises(Invalid):
        act.check({"do": "roll", "sides": 1}, Call())
