from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema


class CompanyAwareJWTScheme(OpenApiAuthenticationExtension):
    """
    OpenAPI authentication extension that registers the 'jwtAuth' Bearer
    security scheme for CompanyAwareJWTAuthentication in Swagger / drf-spectacular.
    """
    target_class = "core.authentication.CompanyAwareJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class CustomAutoSchema(AutoSchema):
    """
    Custom AutoSchema that allows DELETE operations to document request bodies
    in OpenAPI / Swagger UI when a request serializer is specified.
    """
    def _get_request_body(self, direction="request"):
        old_method = self.method
        if self.method == "DELETE":
            self.method = "POST"
        try:
            return super()._get_request_body(direction)
        finally:
            self.method = old_method
