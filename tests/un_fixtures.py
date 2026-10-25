"""
tests/un_fixtures.py
--------------------
A stand-in for MAS's page of designated lists and the UN pages and XML files
it leads to, serving the excerpts in tests/fixtures/mas and tests/fixtures/un.
A test changes what is served, or takes a URL down, and the sync reads it
through a real httpx client: no network.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from gldb.config import SANCTIONS_SOURCE_URL

FIXTURES = Path(__file__).parent / "fixtures"

#: The UN page MAS links to for each list in the MAS fixture, and the keyword
#: of the XML list that page offers
UN_PAGES = {
    "https://www.un.org/securitycouncil/sanctions/1718/materials": "dprk",
    "https://www.un.org/securitycouncil/sanctions/2206/materials": "southsudan",
    "https://www.un.org/securitycouncil/sanctions/1591/materials": "sudan",
    "https://www.un.org/securitycouncil/sanctions/1267/aq_sanctions_list": "al-qaida",
    "https://www.un.org/securitycouncil/sanctions/1988/materials": "taliban",
}
XML_URL = "https://scsanctions.un.org/xml/en/{}"


class UnServer:
    """MAS's page, the UN pages and the XML lists, as the fixtures hold them."""

    def __init__(self) -> None:
        self.mas = (FIXTURES / "mas" / "lists.html").read_text(encoding="utf-8")
        self.xml = {k: (FIXTURES / "un" / f"{k}.xml").read_text(encoding="utf-8") for k in UN_PAGES.values()}
        #: URLs that answer with this status instead of their page
        self.fail: dict[str, int] = {}
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.fail:
            return httpx.Response(self.fail[url])
        if url == SANCTIONS_SOURCE_URL:
            return httpx.Response(200, text=self.mas)
        if url in UN_PAGES:  # as the real pages do, offer the Russian list beside the English
            keyword = UN_PAGES[url]
            return httpx.Response(200, text=(
                f'<a href="{XML_URL.format(keyword)}">Xml</a> <a href="{XML_URL.format(keyword).replace("/en/", "/ru/")}">Xml</a>'))
        for keyword, xml in self.xml.items():
            if url == XML_URL.format(keyword):
                return httpx.Response(200, text=xml)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=True)
