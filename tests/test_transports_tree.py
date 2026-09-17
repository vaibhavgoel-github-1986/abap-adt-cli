from __future__ import annotations

from xml.etree import ElementTree

import pytest

from adt_cli import transports
from adt_cli.commands.transports_cmd import _only, _pair, _wanted
from adt_cli.errors import AbapCliError, ConfigError

TREE = """<?xml version="1.0" encoding="utf-8"?>
<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm">
 <tm:workbench tm:category="Workbench">
  <tm:modifiable tm:status="Modifiable">
   <tm:request tm:number="DEVK900001" tm:owner="ME" tm:desc="open one" tm:type="K"
     tm:status="D" tm:target="DHA" tm:lastchanged_timestamp="20260101120000">
    <tm:task tm:number="DEVK900002" tm:owner="ME" tm:status="D"/>
   </tm:request>
  </tm:modifiable>
  <tm:released tm:status="Released">
   <tm:request tm:number="DEVK900003" tm:owner="ME" tm:desc="done one" tm:type="K"
     tm:status="R"/>
  </tm:released>
 </tm:workbench>
 <tm:customizing tm:category="Customizing">
  <tm:modifiable tm:status="Modifiable">
   <tm:request tm:number="DEVK900004" tm:owner="ME" tm:desc="config" tm:type="W"
     tm:status="D"/>
  </tm:modifiable>
 </tm:customizing>
</tm:root>
"""


def _by_number() -> dict[str, transports.Request]:
    return {entry.number: entry for entry in transports._parse_tree(TREE)}


def test_the_tree_yields_every_request():
    assert sorted(_by_number()) == ["DEVK900001", "DEVK900003", "DEVK900004"]


def test_the_enclosing_section_decides_category_and_status():
    found = _by_number()
    assert (found["DEVK900001"].category, found["DEVK900001"].status) == (
        "workbench",
        "modifiable",
    )
    assert found["DEVK900003"].status == "released"
    assert found["DEVK900004"].category == "customizing"


def test_tasks_attach_to_the_request_that_holds_them():
    found = _by_number()
    assert [task.number for task in found["DEVK900001"].tasks] == ["DEVK900002"]
    assert found["DEVK900003"].tasks == ()


def test_a_request_keeps_its_description_and_target():
    entry = _by_number()["DEVK900001"]
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
    workbench = _by_number()["DEVK900001"]
    customizing = _by_number()["DEVK900004"]
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


REQUEST_DOC = """<?xml version="1.0" encoding="utf-8"?>
<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm">
 <tm:request tm:number="DEVK900001">
  <tm:attributes tm:attribute="Z_TICKET" tm:description="Ticket reference"
    tm:value="ABC-1234" tm:position="000002"/>
  <tm:attributes tm:attribute="Z_DEPLOYMENT" tm:description="Deployment reference"
    tm:value="ABC-9999" tm:position="000001"/>
 </tm:request>
</tm:root>
"""

CATALOGUE = """<?xml version="1.0" encoding="utf-8"?>
<nameditem:namedItemList xmlns:nameditem="http://www.sap.com/adt/nameditem">
 <nameditem:totalItemCount>2</nameditem:totalItemCount>
 <nameditem:namedItem><nameditem:name>Z_TICKET</nameditem:name>
  <nameditem:description>Ticket reference</nameditem:description>
  <nameditem:data/></nameditem:namedItem>
 <nameditem:namedItem><nameditem:name>GIT_BRANCH</nameditem:name>
  <nameditem:description/><nameditem:data/></nameditem:namedItem>
</nameditem:namedItemList>
"""


def test_attributes_come_back_in_position_order():
    found = transports._parse_attributes(REQUEST_DOC)
    assert [entry.name for entry in found] == ["Z_DEPLOYMENT", "Z_TICKET"]
    assert found[1].value == "ABC-1234"
    assert found[1].position == "000002"
    assert found[1].description == "Ticket reference"


def test_a_new_attribute_is_sent_without_a_position():
    payload = transports._attribute_payload(
        "DEVK900001", "addattribute", transports.Attribute("Z_TICKET", "ABC-1")
    )
    assert 'tm:useraction="addattribute"' in payload
    assert "tm:position" not in payload


def test_an_existing_attribute_is_addressed_by_position():
    payload = transports._attribute_payload(
        "DEVK900001",
        "modifyattribute",
        transports.Attribute("Z_TICKET", "ABC-2", position="000002"),
    )
    assert 'tm:useraction="modifyattribute"' in payload
    assert 'tm:position="000002"' in payload


def test_the_catalogue_keeps_attributes_without_a_description():
    root = ElementTree.fromstring(CATALOGUE)
    assert root is not None  # the sample is well-formed XML
    found = transports._NAMED_ITEM.findall(CATALOGUE)
    names = [name.strip() for name, _ in found]
    assert names == ["Z_TICKET", "GIT_BRANCH"]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Z_TICKET=ABC-1234", ("Z_TICKET", "ABC-1234")),
        ("z_ticket = spaced ", ("Z_TICKET", "spaced")),
        ("Z_X=a=b", ("Z_X", "a=b")),
        ("Z_EMPTY=", ("Z_EMPTY", "")),
    ],
)
def test_name_value_pairs_split_on_the_first_equals(token, expected):
    assert _pair(token) == expected


@pytest.mark.parametrize("token", ["Z_TICKET", "=value", ""])
def test_a_malformed_pair_is_refused(token):
    with pytest.raises(AbapCliError, match="NAME=VALUE"):
        _pair(token)
