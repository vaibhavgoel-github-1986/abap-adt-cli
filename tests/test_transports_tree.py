from __future__ import annotations

from xml.etree import ElementTree

import pytest

from adt_cli import transports
from adt_cli.commands.transports_cmd import _only, _wanted
from adt_cli.errors import ConfigError

TREE = """<?xml version="1.0" encoding="utf-8"?>
<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm">
 <tm:workbench tm:category="Workbench">
  <tm:modifiable tm:status="Modifiable">
   <tm:request tm:number="DHAK900001" tm:owner="ME" tm:desc="open one" tm:type="K"
     tm:status="D" tm:target="DHA" tm:lastchanged_timestamp="20260101120000">
    <tm:task tm:number="DHAK900002" tm:owner="ME" tm:status="D"/>
   </tm:request>
  </tm:modifiable>
  <tm:released tm:status="Released">
   <tm:request tm:number="DHAK900003" tm:owner="ME" tm:desc="done one" tm:type="K"
     tm:status="R"/>
  </tm:released>
 </tm:workbench>
 <tm:customizing tm:category="Customizing">
  <tm:modifiable tm:status="Modifiable">
   <tm:request tm:number="DHAK900004" tm:owner="ME" tm:desc="config" tm:type="W"
     tm:status="D"/>
  </tm:modifiable>
 </tm:customizing>
</tm:root>
"""


def _by_number() -> dict[str, transports.Request]:
    return {entry.number: entry for entry in transports._parse_tree(TREE)}


def test_the_tree_yields_every_request():
    assert sorted(_by_number()) == ["DHAK900001", "DHAK900003", "DHAK900004"]


def test_the_enclosing_section_decides_category_and_status():
    found = _by_number()
    assert (found["DHAK900001"].category, found["DHAK900001"].status) == (
        "workbench",
        "modifiable",
    )
    assert found["DHAK900003"].status == "released"
    assert found["DHAK900004"].category == "customizing"


def test_tasks_attach_to_the_request_that_holds_them():
    found = _by_number()
    assert [task.number for task in found["DHAK900001"].tasks] == ["DHAK900002"]
    assert found["DHAK900003"].tasks == ()


def test_a_request_keeps_its_description_and_target():
    entry = _by_number()["DHAK900001"]
    assert entry.description == "open one"
    assert entry.target == "DHA"
    assert entry.owner == "ME"


def test_an_empty_tree_is_not_an_error():
    assert transports._parse_tree('<tm:root xmlns:tm="urn:x"/>') == []


@pytest.mark.parametrize(
    ("yes", "no", "expected"),
    [(False, False, None), (True, True, None), (True, False, True), (False, True, False)],
)
def test_opposing_flags_collapse_to_one_filter(yes, no, expected):
    assert _only(yes, no) is expected


def test_the_category_filter_keeps_only_its_side():
    workbench = _by_number()["DHAK900001"]
    customizing = _by_number()["DHAK900004"]
    assert _wanted(workbench, None) and _wanted(customizing, None)
    assert _wanted(customizing, True) and not _wanted(workbench, True)
    assert _wanted(workbench, False) and not _wanted(customizing, False)


def test_a_new_request_carries_the_description_and_type():
    payload = transports._new_payload("fix the thing", transports.CUSTOMIZING, "DHA", "ME")
    assert 'tm:useraction="newrequest"' in payload
    assert 'tm:desc="fix the thing"' in payload
    assert 'tm:type="W"' in payload
    assert 'tm:target="DHA"' in payload
    assert 'tm:owner="ME"' in payload


def test_a_quote_in_the_description_survives_as_written():
    text = 'say "hi" & <go>'
    payload = transports._new_payload(text, transports.WORKBENCH, "", "ME")
    root = ElementTree.fromstring(payload)
    request = root.find("{http://www.sap.com/cts/adt/tm}request")
    assert request.get("{http://www.sap.com/cts/adt/tm}desc") == text


class _Session:
    user = "ME"

    async def post(self, *_, **__):
        raise AssertionError("SAP must not be called for an invalid description")


async def test_a_description_is_required():
    with pytest.raises(ConfigError, match="needs a description"):
        await transports.create(_Session(), "   ")


async def test_an_over_long_description_is_refused_before_sending():
    with pytest.raises(ConfigError, match="at most 60"):
        await transports.create(_Session(), "x" * 61)
