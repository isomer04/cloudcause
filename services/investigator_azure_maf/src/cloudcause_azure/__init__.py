"""CloudCause Azure specialist, built on Microsoft Agent Framework."""

from .investigator import AzureInvestigator
from .playbooks import AZURE_PLAYBOOKS

__all__ = ["AZURE_PLAYBOOKS", "AzureInvestigator"]
