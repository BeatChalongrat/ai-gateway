"""ETDA e-Tax Invoice XML (ขมธอ. 3-2560 shape, CrossIndustryInvoice) for fixtures.

Namespaces and element names follow the ETDA standard's structure
(rsm:TaxInvoice_CrossIndustryInvoice → ExchangedDocument /
SupplyChainTradeTransaction); only the elements an AP clerk needs are
written. Document type codes: 380 invoice, 388 tax invoice, T01 receipt (ใบรับ),
T02 invoice/tax invoice, T03 receipt/tax invoice, T04 delivery order/tax
invoice, 80 debit note, 81 credit note.
"""

from __future__ import annotations

from xml.sax.saxutils import escape as x

NS_RSM = "urn:etda:uncefact:data:standard:TaxInvoice_CrossIndustryInvoice:2"
NS_RAM = "urn:etda:uncefact:data:standard:TaxInvoice_ReusableAggregateBusinessInformationEntity:2"


def _party(tag: str, p: dict) -> str:
    branch = p.get("branch") or "00000"
    return f"""      <ram:{tag}>
        <ram:Name>{x(p['name'])}</ram:Name>
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="TXID">{x(p['tax_id'])}{x(branch)}</ram:ID></ram:SpecifiedTaxRegistration>
        <ram:PostalTradeAddress><ram:LineOne>{x(p['address'])}</ram:LineOne><ram:CountryID>TH</ram:CountryID></ram:PostalTradeAddress>
      </ram:{tag}>"""


def build(spec, seller, buyer) -> bytes:
    t = spec["totals"]
    lines = "".join(f"""
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>{i}</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:ID>{x(l['code'])}</ram:ID><ram:Name>{x(l['description'])}</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement><ram:GrossPriceProductTradePrice><ram:ChargeAmount>{l['unit_price']}</ram:ChargeAmount></ram:GrossPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="{x(l['unit'] or 'EA')}">{l['quantity']}</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement><ram:SpecifiedTradeSettlementLineMonetarySummation><ram:NetLineTotalAmount currencyID="THB">{l['amount']}</ram:NetLineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>""" for i, l in enumerate(spec["lines"], 1))
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:TaxInvoice_CrossIndustryInvoice xmlns:rsm="{NS_RSM}" xmlns:ram="{NS_RAM}">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter><ram:ID schemeAgencyID="ETDA" schemeVersionID="v2.0">ER3-2560</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{x(spec['number'])}</ram:ID>
    <ram:Name>{x(spec['title'])}</ram:Name>
    <ram:TypeCode>{x(spec.get('etax_type', '388'))}</ram:TypeCode>
    <ram:IssueDateTime>{spec['date'].isoformat()}T00:00:00.0</ram:IssueDateTime>
    <ram:CreationDateTime>{spec['date'].isoformat()}T09:00:00.0</ram:CreationDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
{_party('SellerTradeParty', seller)}
{_party('BuyerTradeParty', buyer)}
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeDelivery/>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode listID="ISO 4217 3A">THB</ram:InvoiceCurrencyCode>
      <ram:ApplicableTradeTax>
        <ram:TypeCode>VAT</ram:TypeCode><ram:CalculatedRate>{t['vat_rate']}</ram:CalculatedRate>
        <ram:BasisAmount currencyID="THB">{t['subtotal']}</ram:BasisAmount><ram:CalculatedAmount currencyID="THB">{t['vat_amount']}</ram:CalculatedAmount>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount currencyID="THB">{t['sum_lines']}</ram:LineTotalAmount>
        <ram:AllowanceTotalAmount currencyID="THB">{t['discount']}</ram:AllowanceTotalAmount>
        <ram:TaxBasisTotalAmount currencyID="THB">{t['subtotal']}</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="THB">{t['vat_amount']}</ram:TaxTotalAmount>
        <ram:GrandTotalAmount currencyID="THB">{t['total']}</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>{lines}
  </rsm:SupplyChainTradeTransaction>
</rsm:TaxInvoice_CrossIndustryInvoice>
"""
    return doc.encode("utf-8")
