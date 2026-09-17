from __future__ import annotations

import json

import pytest

from adt_cli import odata
from adt_cli.errors import ConfigError


def test_a_v4_service_is_published_under_its_binding():
    assert odata.service_root("ZSD_EXAMPLE_API", namespace="ZSB_EXAMPLE_API") == (
        "/sap/opu/odata4/sap/zsb_example_api/srvd_a2x/sap/zsd_example_api/0001"
    )


def test_a_v4_service_falls_back_to_its_own_name_as_the_binding():
    assert odata.service_root("ZSD_EXAMPLE_API").endswith(
        "/zsd_example_api/srvd_a2x/sap/zsd_example_api/0001"
    )


def test_a_v2_service_sits_under_the_gateway_root():
    assert odata.service_root("zmm_example_srv", version="v2") == (
        "/sap/opu/odata/sap/ZMM_EXAMPLE_SRV"
    )


def test_an_entity_is_appended_and_an_empty_one_is_not():
    root = odata.service_root("ZSD_EXAMPLE_API", namespace="ZSB_EXAMPLE_API")
    assert odata.uri_for("ZSD_EXAMPLE_API", "Items", namespace="ZSB_EXAMPLE_API") == f"{root}/Items"
    assert odata.uri_for("ZSD_EXAMPLE_API", "", namespace="ZSB_EXAMPLE_API") == root


def test_a_missing_service_name_is_refused():
    with pytest.raises(ConfigError, match="service name"):
        odata.service_root("  ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["$top=10"], {"$top": "10"}),
        (["$filter=Name eq 'a'"], {"$filter": "Name eq 'a'"}),
        (["$filter=a eq 'x=y'"], {"$filter": "a eq 'x=y'"}),
        (["$select="], {"$select": ""}),
    ],
)
def test_query_options_split_on_the_first_equals(raw, expected):
    assert odata.parse_query(raw) == expected


@pytest.mark.parametrize("raw", ["$top", "=10", ""])
def test_a_malformed_query_option_is_refused(raw):
    with pytest.raises(ConfigError, match="KEY=VALUE"):
        odata.parse_query([raw])


def test_a_body_may_be_inline_json():
    assert odata.parse_body('{"a": 1}') == {"a": 1}


def test_a_body_may_come_from_a_file(tmp_path):
    target = tmp_path / "payload.json"
    target.write_text('{"b": 2}', encoding="utf-8")
    assert odata.parse_body(f"@{target}") == {"b": 2}


def test_a_bad_body_is_refused_before_sending():
    with pytest.raises(ConfigError, match="not valid JSON"):
        odata.parse_body("{nope}")


def test_a_missing_body_file_is_reported():
    with pytest.raises(ConfigError, match="cannot read"):
        odata.parse_body("@/no/such/file.json")


def test_the_v4_error_message_is_lifted_out():
    body = json.dumps({"error": {"code": "X/000", "message": "No filters were provided"}})
    assert odata.explain(body) == "No filters were provided"


def test_the_v2_error_message_is_unwrapped():
    body = json.dumps({"error": {"message": {"lang": "en", "value": "Field unknown"}}})
    assert odata.explain(body) == "Field unknown"


@pytest.mark.parametrize("body", ["", "not json", "{}", '{"error": "flat"}'])
def test_a_body_with_no_message_explains_nothing(body):
    assert odata.explain(body) == ""


def test_rows_are_found_in_both_dialects():
    assert odata.rows({"value": [{"a": 1}]}) == [{"a": 1}]
    assert odata.rows({"d": {"results": [{"a": 1}]}}) == [{"a": 1}]
    assert odata.rows({"d": [{"a": 1}]}) == [{"a": 1}]


def test_a_single_entity_is_not_a_result_set():
    assert odata.rows({"name": "one"}) is None
    assert odata.rows("plain text") is None


class _Session:
    user = "ME"

    async def request(self, *_, **__):
        raise AssertionError("SAP must not be called for invalid arguments")


async def test_an_unknown_method_is_refused_before_connecting():
    with pytest.raises(ConfigError, match="not an HTTP method"):
        await odata.call(_Session(), "FETCH", "ZSD_EXAMPLE_API")


async def test_an_unknown_version_is_refused():
    with pytest.raises(ConfigError, match="not an OData version"):
        await odata.call(_Session(), "GET", "ZSD_EXAMPLE_API", version="v3")


async def test_a_body_on_a_read_is_refused():
    with pytest.raises(ConfigError, match="no request body"):
        await odata.call(_Session(), "GET", "ZSD_EXAMPLE_API", "Items", body={"a": 1})
