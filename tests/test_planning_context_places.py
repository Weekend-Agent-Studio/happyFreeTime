from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.api.planning_context import PlanningContextApplication
from app.api.schemas import PlanningContextPatch
from app.domain.constraints import ConstraintSource, ConstraintValue, GeoLocation, PlanRequest
from app.domain.providers import ProviderMode, ProviderSource
from app.providers.geocoding import MockGeocodingProvider
from app.services.enrichment import EnvironmentContext


@pytest.fixture
def environment() -> EnvironmentContext:
    return EnvironmentContext(
        now=datetime(2026, 10, 22, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        default_location=GeoLocation(
            city="北京市",
            district="朝阳区",
            address="北京市朝阳区",
            latitude=39.9219,
            longitude=116.4436,
            adcode="110105",
        ),
    )


@pytest.fixture
def geocoder() -> MockGeocodingProvider:
    return MockGeocodingProvider.from_locations({
        ("北京市", "国贸"): (39.9087, 116.4615, "朝阳区", "110105", "北京市朝阳区国贸"),
    })


def test_search_returns_displayable_identity_without_coordinates(geocoder: MockGeocodingProvider) -> None:
    application = PlanningContextApplication(geocoding_provider=geocoder)

    result = application.search_departure_locations(" 国贸 ")

    assert result.city == "北京市"
    assert result.source == ProviderSource.MOCK
    assert result.mode == ProviderMode.MOCK
    assert len(result.results) == 1
    option = result.results[0]
    assert option.name == "国贸"
    assert option.provider_id.startswith("mock:")
    assert not hasattr(option, "point")
    assert "latitude" not in option.model_dump()


def test_selected_provider_id_is_resolved_by_server_and_compiled_to_route_location(
    environment: EnvironmentContext,
    geocoder: MockGeocodingProvider,
) -> None:
    application = PlanningContextApplication(geocoding_provider=geocoder)
    option = application.search_departure_locations("国贸").results[0]
    patch = PlanningContextPatch.model_validate({
        "base_revision": 7,
        "where": {"operation": "set", "value": {"kind": "provider_place", "provider_id": option.provider_id}},
    })

    compiled = application.compile_edit(patch, request=PlanRequest(revision=7), environment=environment)
    saved = compiled.patch.set_fields["location"]

    assert compiled.issues == ()
    assert saved.source == ConstraintSource.USER_EXPLICIT
    assert saved.value.name == option.name
    assert saved.value.address == option.address
    assert saved.value.provider_place_id == option.provider_id
    assert (saved.value.latitude, saved.value.longitude) == (39.9087, 116.4615)


def test_unknown_provider_id_creates_no_location_patch_and_client_coordinates_are_rejected(
    environment: EnvironmentContext,
    geocoder: MockGeocodingProvider,
) -> None:
    application = PlanningContextApplication(geocoding_provider=geocoder)
    unknown_patch = PlanningContextPatch.model_validate({
        "base_revision": 3,
        "where": {"operation": "set", "value": {"kind": "provider_place", "provider_id": "mock:forged"}},
    })
    unknown = application.compile_edit(unknown_patch, request=PlanRequest(revision=3), environment=environment)

    assert unknown.patch.set_fields == {}
    assert len(unknown.issues) == 1
    assert unknown.issues[0].code == "LOCATION_PLACE_NOT_FOUND"
    with pytest.raises(ValidationError):
        PlanningContextPatch.model_validate({
            "base_revision": 3,
            "where": {"operation": "set", "value": {
                "kind": "provider_place", "provider_id": "mock:forged", "latitude": 0, "longitude": 0,
            }},
        })


def test_default_selection_restores_exact_environment_default(environment: EnvironmentContext) -> None:
    application = PlanningContextApplication()
    current = ConstraintValue(
        value=GeoLocation(
            name="当前地点",
            city="北京市",
            district="海淀区",
            address="北京市海淀区当前地点",
            latitude=39.9756,
            longitude=116.3176,
            provider_place_id="mock:current",
        ),
        source=ConstraintSource.USER_EXPLICIT,
        rule_id="planning_context.where.selected_place.v1",
    )
    request = PlanRequest(revision=5, location=current)
    patch = PlanningContextPatch.model_validate({
        "base_revision": 5,
        "where": {"operation": "set", "value": {"kind": "default"}},
    })

    compiled = application.compile_edit(patch, request=request, environment=environment)
    saved = compiled.patch.set_fields["location"]

    assert saved.source == ConstraintSource.SYSTEM_CONTEXT
    assert saved.value == environment.default_location
    assert saved.rule_id == "location.session_default.v1"
