"""P5-01: the routing pieces the inventory named exist and are callable."""


class TestCurrentRoutingServiceExists:
    def test_routing_service_importable(self):
        from app.services.routing_service import RoutingService
        svc = RoutingService()
        assert hasattr(svc, "select_route")

    def test_model_registry_importable(self):
        from app.services.providers.model_registry import resolve_provider, list_models
        assert resolve_provider("gpt-4o") == "openai"
        assert len(list_models()) > 0

    def test_route_decision_importable(self):
        from app.domain.entities import RouteDecision
        rd = RouteDecision(provider_name="openai", model_name="gpt-4o")
        assert rd.provider_name == "openai"
