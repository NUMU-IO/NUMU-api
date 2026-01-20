"""Use case for getting supported services information."""

from typing import Optional

from src.api.v1.schemas.tenant.configuration import (
    ServiceInfoResponse,
    SupportedServicesResponse,
)
from src.infrastructure.database.models.tenant.configuration import (
    ServiceType,
    ServiceName,
)
from src.infrastructure.external_services.gateway_validators import (
    get_validator_factory,
)


# Service display information
SERVICE_INFO = {
    # Payment Gateways
    ServiceName.FAWRY: {
        "display_name": "Fawry",
        "description": "Egypt's leading payment network. Supports reference payments, card payments, and Fawry Pay wallet.",
        "documentation_url": "https://developer.fawry.io",
    },
    ServiceName.PAYMOB: {
        "display_name": "Paymob",
        "description": "Egypt's payment infrastructure provider. Supports cards, mobile wallets, installments, and BNPL.",
        "documentation_url": "https://docs.paymob.com",
    },
    ServiceName.VODAFONE_CASH: {
        "display_name": "Vodafone Cash",
        "description": "Egypt's leading mobile wallet service for instant payments.",
        "documentation_url": None,
    },
    ServiceName.STRIPE: {
        "display_name": "Stripe",
        "description": "International payment processing for cards and digital wallets.",
        "documentation_url": "https://stripe.com/docs",
    },
    ServiceName.TAP: {
        "display_name": "Tap Payments",
        "description": "MENA region payment gateway supporting cards and local payment methods.",
        "documentation_url": "https://docs.tap.company",
    },
    # Shipping Carriers
    ServiceName.ARAMEX: {
        "display_name": "Aramex",
        "description": "International and regional shipping with COD support.",
        "documentation_url": "https://www.aramex.com/developers",
    },
    ServiceName.BOSTA: {
        "display_name": "Bosta",
        "description": "Egypt's leading last-mile delivery company with same-day delivery.",
        "documentation_url": "https://developers.bosta.co",
    },
    ServiceName.MYLERZ: {
        "display_name": "MylerZ",
        "description": "Egyptian e-commerce logistics provider with fulfillment services.",
        "documentation_url": None,
    },
    # Communication
    ServiceName.WHATSAPP_BUSINESS: {
        "display_name": "WhatsApp Business API",
        "description": "Customer messaging via WhatsApp for order updates and support.",
        "documentation_url": "https://developers.facebook.com/docs/whatsapp",
    },
    ServiceName.TWILIO: {
        "display_name": "Twilio",
        "description": "SMS and voice communications for notifications and verification.",
        "documentation_url": "https://www.twilio.com/docs",
    },
}


class GetSupportedServicesUseCase:
    """Use case for getting information about supported services."""
    
    def __init__(self):
        self.validator_factory = get_validator_factory()
    
    async def execute(self) -> SupportedServicesResponse:
        """Get all supported services grouped by type.
        
        Returns:
            SupportedServicesResponse with all services
        """
        supported = self.validator_factory.get_supported_services()
        
        payment_gateways = []
        shipping_carriers = []
        communication = []
        
        for service_type, service_names in supported.items():
            for service_name in service_names:
                info = await self._build_service_info(service_type, service_name)
                
                if service_type == ServiceType.PAYMENT_GATEWAY:
                    payment_gateways.append(info)
                elif service_type == ServiceType.SHIPPING_CARRIER:
                    shipping_carriers.append(info)
                elif service_type in [ServiceType.WHATSAPP, ServiceType.SMS]:
                    communication.append(info)
        
        return SupportedServicesResponse(
            payment_gateways=payment_gateways,
            shipping_carriers=shipping_carriers,
            communication=communication,
        )
    
    async def get_service_info(
        self,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> Optional[ServiceInfoResponse]:
        """Get information about a specific service.
        
        Args:
            service_type: Type of service
            service_name: Specific service provider
        
        Returns:
            ServiceInfoResponse if service exists, None otherwise
        """
        if not self.validator_factory.is_supported(service_type, service_name):
            return None
        
        return await self._build_service_info(service_type, service_name)
    
    async def _build_service_info(
        self,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> ServiceInfoResponse:
        """Build service info response.
        
        Args:
            service_type: Type of service
            service_name: Specific service provider
        
        Returns:
            ServiceInfoResponse with service details
        """
        info = SERVICE_INFO.get(service_name, {})
        
        required_fields = self.validator_factory.get_required_fields(
            service_type, service_name
        )
        optional_fields = self.validator_factory.get_optional_fields(
            service_type, service_name
        )
        
        return ServiceInfoResponse(
            service_type=service_type,
            service_name=service_name,
            display_name=info.get("display_name", service_name.value),
            description=info.get("description", ""),
            required_fields=required_fields,
            optional_fields=optional_fields,
            documentation_url=info.get("documentation_url"),
        )
