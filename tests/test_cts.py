from __future__ import annotations

import pytest

from adt_cli import cts
from adt_cli.errors import ConfigError, ConflictError
from adt_cli.repository import RepoObject

HELD = cts.Holder(request="DHAK900123", owner="BOB", text="work", tasks=(("DHAK900124", "ANN"),))


def test_normalise_request_accepts_a_real_id():
    assert cts.normalise_request(" dhak900123 ") == "DHAK900123"


def test_normalise_request_rejects_nonsense():
    with pytest.raises(ConfigError, match="not a transport request id"):
        cts.normalise_request("please-transport-it")


def test_holder_accepts_its_own_request_and_tasks():
    assert HELD.accepts("dhak900123")
    assert HELD.accepts("DHAK900124")
    assert not HELD.accepts("DHAK900999")


def test_reconcile_adopts_the_request_that_already_holds_the_objects():
    plan = cts.reconcile(
        ["src/a.abap"], [HELD], requested="", package="ZTEST", user="ANN"
    )
    assert plan.request == "DHAK900123"
    assert any("DHAK900124" in note for note in plan.notes)


def test_reconcile_refuses_a_transport_that_clashes():
    with pytest.raises(ConflictError, match="already locked"):
        cts.reconcile(
            ["src/a.abap"], [HELD], requested="DHAK900999", package="ZTEST", user="ANN"
        )


def test_reconcile_refuses_objects_spread_over_several_requests():
    other = cts.Holder(request="DHAK900500", owner="ANN", text="")
    with pytest.raises(ConflictError, match="several"):
        cts.reconcile(
            ["src/a.abap", "src/b.abap"],
            [HELD, other],
            requested="",
            package="ZTEST",
            user="ANN",
        )


def test_reconcile_demands_a_transport_for_a_transportable_package():
    with pytest.raises(ConfigError, match="--transport"):
        cts.reconcile(["src/a.abap"], [None], requested="", package="ZTEST", user="ANN")


def test_reconcile_allows_a_local_package_without_a_transport():
    plan = cts.reconcile(["src/a.abap"], [None], requested="", package="$TMP", user="ANN")
    assert plan.request == ""


def test_payload_escapes_object_names():
    payload = cts._payload(RepoObject("ZCL_A&B", "CLAS/OC", "/uri?a=1&b=2"))
    assert "&amp;" in payload
    assert "ZCL_A&B" not in payload
