"""Select one reviewed-order destination; never silently fall back to another ERP."""
import os


def from_env(backend=None):
    selected = backend or os.getenv('INTAKE_ERP', 'erpnext')
    if selected == 'erpnext':
        from .api import Client
        from .intake import ERPNextOrders
        return ERPNextOrders(Client.from_env())
    if selected == 'business-central':
        from .business_central import BusinessCentralOrders
        return BusinessCentralOrders.from_env()
    raise ValueError('INTAKE_ERP must be erpnext or business-central')
