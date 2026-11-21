import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from distress_signals.sources import issuer_disclosures as filings
from distress_signals.util import SourceError


class IssuerDisclosures(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def transaction(self, name, day, status='Current'):
        index = self.folder / name
        index.write_text(f'''<ContinuingDisclosureSubscriptionDataMessage xmlns="urn:msrb">
          <ContinuingDisclosureSubmission SubmissionIdentifier="S1" SubmissionStatus="Publish"
            SubmissionTransactionDateTime="2026-06-{day}T08:00:00-04:00">
            <DisclosureIndexingData><Disclosure EventDisclosureCategory="BondCall">
              <DisclosurePeriod AsOfDate="2026-09-01"/></Disclosure>
              <Securities><Security IssuerName="Building Authority" CUSIP9="111222333"/></Securities>
            </DisclosureIndexingData>
            <ContactInformation ContactType="Submitter" ContactOrganizationName="Filing Agent"/>
            <ContactInformation ContactType="Obligor" ContactOrganizationName="Facility Trust"/>
            <Document DocumentIdentifier="D1" DocumentPostingDate="2026-06-01" DocumentStatus="{status}">
              <UnderlyingFiles><File FileIdentifier="F1" FileStatus="{status}"/></UnderlyingFiles>
            </Document>
          </ContinuingDisclosureSubmission></ContinuingDisclosureSubscriptionDataMessage>''')

    def test_msrb_projection_pdf_and_removal(self):
        self.transaction('a.xml', '02')
        with patch.dict(os.environ, {'EMMA_DISCLOSURE_DIR': self.tmp.name}):
            result, = filings.search('emma', 'Facility', since='2026-06-01')['results']
            self.assertEqual(result['issuers'], ['Building Authority'])
            self.assertEqual(result['obligors'], ['Facility Trust'])
            self.assertEqual(result['published'], '2026-06-01')
            self.assertEqual(result['disclosures'][0]['periods'][0]['AsOfDate'], '2026-09-01')
            native = result['record_id'].partition(':')[2]
            self.assertEqual(filings.read('emma', native)['kind'], 'filing_index')
            (self.folder / 'F1.pdf').write_bytes(b'%PDF-')
            with patch.object(filings.web, '_pdf', return_value=('[page 1]\nRedemption terms', '')):
                self.assertIn('Redemption terms', filings.read('emma', native)['text'])
            self.transaction('b.xml', '04', 'Removed')
            self.assertEqual(filings.search('emma', '')['results'], [])
            with self.assertRaises(SourceError):
                filings.read('emma', native)

    def dds_index(self, rel='notice.pdf', url='https://www.sedarplus.ca/csa-party/records/document.html?id=123'):
        path = self.folder / 'catalog.csv'
        with path.open('w', newline='') as stream:
            w = csv.DictWriter(stream, fieldnames=sorted(filings._DDS_FIELDS))
            w.writeheader()
            w.writerow({'document_id': 'reg/79', 'issuer': 'Industrial Co.', 'title': 'Notice',
                        'published': '2026-05-04T15:00:00-04:00', 'url': url, 'file': rel})
        return str(path)

    def test_dds_reads_original_bytes_and_revalidates_current_catalog(self):
        with patch.dict(os.environ, {'SEDAR_DDS_INDEX': self.dds_index()}):
            row, = filings.search('sedar_plus', 'Industrial')['results']
            native = row['record_id'].partition(':')[2]
            with self.assertRaisesRegex(SourceError, 'missing'):
                filings.read('sedar_plus', native)
            (self.folder / 'notice.pdf').write_bytes(b'%PDF-1.5 test')
            with patch.object(filings.web, '_pdf', return_value=('[page 1]\nIssuer document', '')):
                self.assertEqual(filings.read('sedar_plus', native)['text'], '[page 1]\nIssuer document')
            self.dds_index(rel='../credentials.pdf')
            with self.assertRaisesRegex(SourceError, 'outside'):
                filings.read('sedar_plus', native)
            self.dds_index(url='https://www.sedarplus.ca/')
            with self.assertRaisesRegex(SourceError, 'document URL'):
                filings.search('sedar_plus', '')

    def test_catalog_rejects_credentials_in_citations(self):
        base = 'https://www.sedarplus.ca/csa-party/records/document.html'
        self.assertTrue(filings._public_document_url(base + '?documentId=4299&lang=fr'))
        with patch.dict(os.environ, {'SEDAR_DDS_INDEX': self.dds_index()}):
            for url in [base + '?id=12&X-Amz-Signature=private',
                        'https://a:private@filings.example/report.pdf',
                        'https://filings.example/report.pdf?sig=private',
                        base + '?id=12%3btoken%3dprivate',
                        base + '?id=2#access_token=private']:
                with self.subTest(url=url):
                    self.dds_index(url=url)
                    with self.assertRaisesRegex(SourceError, 'public document URL') as err:
                        filings.search('sedar_plus', '')
                    self.assertNotIn('private', str(err.exception))

    def test_cse_filing_access_and_refresh(self):
        meta = {'security_name': 'Issuer One', 'sedar_filings': 'https://webapi.thecse.com/trading/listed/sedar_filings/100.json'}
        page = {'props': {'pageProps': {'staticCompanyData': {'metadata': meta}}}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(page) + '</script>'
        raw = {'document_description': 'Material change', 'url': 'https://sedar-filings.thecse.com/100/22.pdf',
               'public_date': '2026-01-05', 'status': 'available', 'accession_number': '22'}
        client = Mock()
        client.get.side_effect = [Mock(text=html), Mock(**{'json.return_value': {'list': [raw]}})] * 2
        with patch.object(filings, 'session', return_value=client), patch.dict(os.environ, {'CSE_SEDAR_LICENSED': ''}):
            with self.assertRaisesRegex(SourceError, 'permission'):
                filings.search('sedar_plus', '', cse_listing='issuer-one')
            client.get.assert_not_called()
            with patch.dict(os.environ, {'CSE_SEDAR_LICENSED': '1'}):
                row, = filings.search('sedar_plus', 'Material', cse_listing='issuer-one')['results']
                self.assertEqual(row['issuer_as_listed'], 'Issuer One')
                with patch.object(filings.web, 'read', return_value={'data': {'content_type': 'application/pdf'},
                                                                    'text': '[page 1]\nOriginal text'}):
                    got = filings.read('sedar_plus', row['record_id'].partition(':')[2])
                    self.assertEqual((got['published'], got['text']), ('2026-01-05', '[page 1]\nOriginal text'))
