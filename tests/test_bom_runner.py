"""Synthetic contract cases and opt-in actual Docker enforcement checks."""

import io
import json
import os
import subprocess
from threading import Event, Timer
import unittest
import zipfile

from service.bom_runner import BomRunner, MAX_INPUT, PARSER_VERSION, _JOBS
from service.errors import ServiceError


def ods(content=None, extra=None):
    if content is None:
        content = '''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table table:name="BOM"><table:table-row><table:table-cell><text:p>Quantity</text:p></table:table-cell><table:table-cell><text:p>Designator</text:p></table:table-cell><table:table-cell table:number-columns-repeated="514"/></table:table-row><table:table-row/><table:table-row><table:table-cell office:value="2"/><table:table-cell><text:p>R1,R2</text:p></table:table-cell><table:table-cell table:number-columns-repeated="514"/></table:table-row><table:table-row table:number-rows-repeated="100000"><table:table-cell table:number-columns-repeated="1024"/></table:table-row></table:table></office:spreadsheet></office:body></office:document-content>'''
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return stream.getvalue()


def text_pdf():
    stream = b"BT /F1 12 Tf 20 100 Td (Quantity Designator) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    output = b"%PDF-1.4\n"
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output += str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        output += f"{offset:010d} 00000 n \n".encode()
    output += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return output


class RunnerContractTests(unittest.TestCase):
    def error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(ServiceError) as error:
            callable_(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_unavailable_never_falls_back_to_host(self):
        runner = BomRunner(image_id="not-an-image")
        self.assertFalse(runner.status()["ready"])
        self.error("RUNNER_UNAVAILABLE", runner.parse, b"a,b\n1,2", "bom.csv")

    def test_input_path_type_and_size_rejected_before_execution(self):
        runner = BomRunner(image_id="not-an-image")
        for filename in ("../../source.csv", "/tmp/source.csv", "..\\source.csv", "x\x00.csv"):
            self.error("PARSER_FILENAME_REJECTED", runner.parse, b"x", filename)
        self.error("PARSER_FORMAT_UNSUPPORTED", runner.parse, b"x", "bom.xlsm")
        self.error("PARSER_INPUT_LIMIT", runner.parse, b"x" * (MAX_INPUT + 1), "bom.csv")

    def test_container_result_schema_is_verified(self):
        good = {"parser_version": PARSER_VERSION, "format": "csv", "sheets": [{"name": "CSV", "rows": [{"row_number": 1, "cells": ["x"]}]}]}
        BomRunner._validate_result(good, "csv")
        for change in ({"format": "ods"}, {"extra": "unexpected"}, {"sheets": [{"name": "CSV", "rows": [{"row_number": True, "cells": ["x"]}]}]}):
            self.error("RUNNER_RESULT_INVALID", BomRunner._validate_result, {**good, **change}, "csv")

    def test_job_concurrency_is_bounded(self):
        _JOBS.acquire(); _JOBS.acquire()
        try:
            self.error("RUNNER_BUSY", BomRunner(image_id="missing").parse, b"x", "bom.csv")
        finally:
            _JOBS.release(); _JOBS.release()


@unittest.skipUnless(os.environ.get("ONTO_BOM_RUNNER_IMAGE"), "actual Docker image not configured")
class SyntheticContainerRunnerTests(unittest.TestCase):
    """Only generated fixtures may use _run for parser/lifecycle diagnostics.

    This does not authorize a real upload on an unenforced host: the public
    parse entry point must still reject it. Enforcement is tested separately.
    """
    error = RunnerContractTests.error
    @classmethod
    def setUpClass(cls):
        cls.runner = BomRunner(timeout_seconds=30)
        cls.runner._check_image()

    def parse_synthetic(self, data, filename):
        kind = filename.rsplit(".", 1)[1]
        result = self.runner._run(data, kind=kind)
        self.runner._validate_result(result, kind)
        return result

    def no_jobs(self, runner=None):
        runner = runner or self.runner
        result = subprocess.run(runner._command() + ["ps", "-aq", "--filter", "label=org.ontologics.broker=" + runner.broker_id],
                                env=runner._environment(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.stdout.strip())

    def test_real_csv_ods_and_text_pdf_preserve_locators(self):
        csv = self.parse_synthetic(b"Quantity,Designator\n2,\"R1,R2\"\n", "bom.csv")
        self.assertEqual(csv["sheets"][0]["rows"][1], {"row_number": 2, "cells": ["2", "R1,R2"]})
        sheet = self.parse_synthetic(ods(), "bom.ods")["sheets"][0]
        self.assertEqual(sheet["rows"], [{"row_number": 1, "cells": ["Quantity", "Designator"]}, {"row_number": 3, "cells": ["2", "R1,R2"]}])
        pdf = self.parse_synthetic(text_pdf(), "bom.pdf")
        self.assertEqual(pdf["sheets"][0]["name"], "page:1")
        self.assertIn("Quantity Designator", pdf["sheets"][0]["rows"][0]["cells"][0])
        self.no_jobs()

    def test_real_archive_external_entity_bomb_and_formula_rejection(self):
        self.error("PARSER_ARCHIVE_REJECTED", self.parse_synthetic, ods(extra={"../escape": b"no"}), "bom.ods")
        self.error("PARSER_XML_ENTITY_REJECTED", self.parse_synthetic, ods('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x/>'), "bom.ods")
        self.error("PARSER_XML_ENCODING_REJECTED", self.parse_synthetic, ods('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x/>'.encode("utf-16")), "bom.ods")
        self.error("PARSER_ARCHIVE_LIMIT", self.parse_synthetic, ods(extra={"oversized": b"0" * (2 * 1024 * 1024)}), "bom.ods")
        self.error("PARSER_MALFORMED", self.parse_synthetic, b"PKcorrupt", "bom.ods")
        with zipfile.ZipFile(io.BytesIO(ods())) as archive:
            xml = archive.read("content.xml").decode()
        self.error("PARSER_FORMULA_REQUIRES_REVIEW", self.parse_synthetic, ods(xml.replace('<table:table-cell>', '<table:table-cell table:formula="of:=1+1">', 1)), "bom.ods")
        self.error("PARSER_EXTERNAL_REFERENCE_REJECTED", self.parse_synthetic, ods(xml.replace('<office:body>', '<office:body xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://invalid.example/">')), "bom.ods")
        self.no_jobs()

    def test_real_odf_whitespace_is_not_lost(self):
        with zipfile.ZipFile(io.BytesIO(ods())) as archive:
            xml = archive.read("content.xml").decode()
        value = 'R1<text:s text:c="2"/>R2<text:tab/>C1<text:line-break/>C2'
        result = self.parse_synthetic(ods(xml.replace("R1,R2", value)), "bom.ods")
        self.assertEqual(result["sheets"][0]["rows"][1]["cells"][1], "R1  R2\tC1\nC2")
        self.error("PARSER_CELL_LIMIT", self.parse_synthetic, ods(xml.replace("R1,R2", '<text:s text:c="99999"/>')), "bom.ods")

    def test_real_public_entry_refuses_unenforced_resource_host(self):
        status = self.runner.status()
        if not status["ready"]:
            self.assertEqual(status["code"], "RUNNER_ENFORCEMENT_UNAVAILABLE")
            self.error("RUNNER_ENFORCEMENT_UNAVAILABLE", self.runner.parse, b"a,b\n1,2", "bom.csv")
            self.no_jobs()
        else:
            self.assertTrue(all(self.runner._enforcement.values()))

    def test_real_resource_and_isolation_enforcement(self):
        status = self.runner.status()
        if not status["ready"]:
            self.skipTest(status["code"])
        result = self.runner._run(b"synthetic-probe", probe="enforcement")
        self.assertTrue(result["ready"])
        self.assertTrue(all(result["checks"].values()))
        self.no_jobs()

    def test_real_timeout_cancel_output_limit_and_cleanup(self):
        short = BomRunner(timeout_seconds=1)
        self.error("RUNNER_TIMEOUT", short._run, b"probe", probe="timeout")
        self.no_jobs(short)
        cancelled = Event()
        timer = Timer(1, cancelled.set)
        timer.start()
        try:
            self.error("RUNNER_CANCELLED", self.runner._run, b"probe", probe="timeout", cancel_event=cancelled)
        finally:
            timer.cancel()
        self.no_jobs()
        self.error("RUNNER_OUTPUT_LIMIT", self.runner._run, b"probe", probe="output")
        self.no_jobs()

    def test_real_source_image_integrity(self):
        bad = BomRunner(image_id="sha256:" + "0" * 64)
        self.assertFalse(bad.status()["ready"])
        self.no_jobs()
