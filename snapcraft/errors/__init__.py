from abc import ABC, abstractmethod
from typing import Optional


class SnapcraftError(Exception):
    """DEPRECATED: Use SnapcraftException instead."""

    fmt = "Daughter classes should redefine this"

    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __str__(self):
        return self.fmt.format([], **self.__dict__)

    def get_exit_code(self):
        """Exit code to use if this exception causes Snapcraft to exit."""
        return 2


class SnapcraftReportableError(SnapcraftError):
    """DEPRECATED: Use SnapcraftException instead with reportable=True."""


class SnapcraftException(Exception, ABC):
    """Base class for Snapcraft Exceptions."""

    @abstractmethod
    def get_brief(self) -> str:
        """Concise, single-line description of the error."""

    @abstractmethod
    def get_resolution(self) -> str:
        """Concise suggestion for user to resolve error."""

    def get_details(self) -> Optional[str]:
        """Detailed technical information, if required for user to debug issue."""
        return None

    def get_docs_url(self) -> Optional[str]:
        """Link to documentation on docs.snapcraft.io, if applicable."""
        return None

    def get_exit_code(self) -> int:
        """Exit code to use when exiting snapcraft due to this exception."""
        return 2

    def get_reportable(self) -> bool:
        """Defines if error is reportable (an exception trace should be shown)."""
        return False

    def __str__(self) -> str:
        return self.get_brief()