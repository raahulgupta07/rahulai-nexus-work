# LDAP Connection Manager
# Licensed under the Business Source License 1.1
# See ENTERPRISE_LICENSE for details

import logging
import ssl
from typing import Optional, List, Dict, Any

from app.settings.dash_config import LDAPConfig

logger = logging.getLogger(__name__)


class LDAPConnectionManager:
    """Shared LDAP connection layer used by both group sync and bind auth.

    ldap3 is imported lazily so the module loads even when ldap3 is not installed.
    """

    def __init__(self, config: LDAPConfig):
        self.config = config
        self._ldap3 = None

    @property
    def ldap3(self):
        if self._ldap3 is None:
            try:
                import ldap3 as _ldap3
                self._ldap3 = _ldap3
            except ImportError:
                raise ImportError(
                    "ldap3 is required for LDAP integration. Install it with: pip install ldap3"
                )
        return self._ldap3

    def _build_server(self):
        tls_config = None
        if self.config.use_ssl or self.config.start_tls:
            tls_config = self.ldap3.Tls(validate=ssl.CERT_NONE)

        return self.ldap3.Server(
            self.config.url,
            use_ssl=self.config.use_ssl,
            tls=tls_config,
            get_info=self.ldap3.ALL,
            connect_timeout=self.config.connection_timeout,
        )

    def get_connection(self):
        """Create a bound service-account connection for search operations."""
        server = self._build_server()
        conn = self.ldap3.Connection(
            server,
            user=self.config.bind_dn,
            password=self.config.bind_password,
            authentication=self.ldap3.SIMPLE,
            auto_bind=True,
            raise_exceptions=True,
        )
        if self.config.start_tls and not self.config.use_ssl:
            conn.start_tls()
        return conn

    def bind_user(self, user_dn: str, password: str) -> bool:
        """Attempt LDAP bind with user's own credentials. Returns True on success."""
        server = self._build_server()
        try:
            conn = self.ldap3.Connection(
                server,
                user=user_dn,
                password=password,
                authentication=self.ldap3.SIMPLE,
                auto_bind=True,
                raise_exceptions=True,
            )
            conn.unbind()
            return True
        except (
            self.ldap3.core.exceptions.LDAPBindError,
            self.ldap3.core.exceptions.LDAPSocketOpenError,
        ):
            return False
        except self.ldap3.core.exceptions.LDAPException as e:
            logger.warning(f"LDAP bind error for {user_dn}: {e}")
            return False

    def find_user_dn(self, email: str) -> Optional[str]:
        """Search for a user by email and return their DN."""
        found = self.find_user(email)
        return found["dn"] if found else None

    def find_user(self, email: str) -> Optional[Dict[str, Any]]:
        """Search for a user by email and return their DN *and* display name.

        ★The name is why this exists. ``find_user_dn`` asked the directory only
        for the email attribute, so the login path had nothing but the address
        to name the account with and used ``email.split("@")[0]``. A directory
        of 200 people arrived as ``staff001``, ``staff002`` … — the real names
        were sitting in the entry the whole time, and ``search_users`` (the
        group-sync path) had always read them. Same search, one more attribute.

        Returns ``None`` when nobody matches, so callers keep the old contract.
        """
        search_base = self.config.user_search_base or self.config.base_dn
        search_filter = f"(&{self.config.user_search_filter}({self.config.user_email_attribute}={email}))"
        name_attr = self.config.user_name_attribute

        conn = self.get_connection()
        try:
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                search_scope=self.ldap3.SUBTREE,
                attributes=[self.config.user_email_attribute, name_attr],
                size_limit=1,
            )
            if not conn.entries:
                return None
            entry = conn.entries[0]
            # ★An attribute the schema does not define is simply absent from the
            # entry — asking for it is not an error and reading it is. Stock
            # OpenLDAP inetOrgPerson has no `displayName`, which was this
            # product's default, so the careless version of this would have
            # raised on the most ordinary directory there is.
            name_val = entry[name_attr].value if name_attr in entry else None
            return {
                "dn": str(entry.entry_dn),
                "name": str(name_val).strip() if name_val else None,
            }
        finally:
            conn.unbind()

    def search_users(self, filter_override: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for all users. Returns list of dicts with dn, email, name."""
        search_base = self.config.user_search_base or self.config.base_dn
        search_filter = filter_override or self.config.user_search_filter
        attrs = [
            self.config.user_email_attribute,
            self.config.user_name_attribute,
        ]

        conn = self.get_connection()
        try:
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                search_scope=self.ldap3.SUBTREE,
                attributes=attrs,
                paged_size=self.config.page_size,
            )

            users = []
            for entry in conn.entries:
                email_val = entry[self.config.user_email_attribute].value if self.config.user_email_attribute in entry else None
                name_val = entry[self.config.user_name_attribute].value if self.config.user_name_attribute in entry else None
                if email_val:
                    users.append({
                        "dn": str(entry.entry_dn),
                        "email": str(email_val).lower(),
                        "name": str(name_val) if name_val else None,
                    })
            return users
        finally:
            conn.unbind()

    def search_groups(self) -> List[Dict[str, Any]]:
        """Search for all groups. Returns list of dicts with dn, name, members."""
        search_base = self.config.group_search_base or self.config.base_dn
        attrs = [
            self.config.group_name_attribute,
            self.config.group_member_attribute,
        ]

        conn = self.get_connection()
        try:
            conn.search(
                search_base=search_base,
                search_filter=self.config.group_search_filter,
                search_scope=self.ldap3.SUBTREE,
                attributes=attrs,
                paged_size=self.config.page_size,
            )

            groups = []
            for entry in conn.entries:
                name_val = entry[self.config.group_name_attribute].value if self.config.group_name_attribute in entry else None
                member_attr = entry[self.config.group_member_attribute] if self.config.group_member_attribute in entry else None
                members = []
                if member_attr and member_attr.value:
                    raw = member_attr.value
                    members = raw if isinstance(raw, list) else [raw]

                if name_val:
                    groups.append({
                        "dn": str(entry.entry_dn),
                        "name": str(name_val),
                        "members": [str(m) for m in members],
                    })
            return groups
        finally:
            conn.unbind()

    def test_connection(self) -> Dict[str, Any]:
        """Test LDAP connectivity and return status info."""
        try:
            conn = self.get_connection()
            server_info = {
                "connected": True,
                "server": self.config.url,
                "vendor": str(conn.server.info.vendor_name) if conn.server.info and conn.server.info.vendor_name else None,
            }
            conn.unbind()
            return server_info
        except Exception as e:
            return {
                "connected": False,
                "server": self.config.url,
                "error": str(e),
            }
