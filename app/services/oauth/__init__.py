"""OAuth 2.1 authorization-server building blocks for the remote MCP.

Increment 1 (this package): the crypto + token core — RS256 signing keys, JWKS,
access-token minting, PKCE verification, refresh-token hashing. No database or
HTTP flow yet; those (grants, /oauth/authorize, /oauth/token) build on this.
"""
