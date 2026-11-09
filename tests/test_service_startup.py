"""Independent loopback instances retain strict runtime and origin validation."""

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from dev.service_stack import load_runtime, StackError
from service.__main__ import parse_args


class ServiceStartupTests(unittest.TestCase):
    def args(self, *extra):
        return parse_args(["--runtime-file", "synthetic-runtime.json", *extra])

    def test_existing_defaults_preserved_without_implicit_initial_import(self):
        args=self.args()
        self.assertEqual((args.origin_host,args.port),("127.0.0.1",8870))
        self.assertEqual((args.postgres_port,args.neo4j_http_port,args.neo4j_bolt_port),(55432,57474,57687))
        self.assertFalse(args.import_initial)
        self.assertIsNone(args.agent_env_file)

    def test_independent_ports_still_require_exact_loopback_runtime(self):
        args=self.args("--origin-host","localhost","--port","8872",
                       "--postgres-port","55433","--neo4j-http-port","57475","--neo4j-bolt-port","57688")
        ports=(args.postgres_port,args.neo4j_http_port,args.neo4j_bolt_port)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"runtime.json"
            config={"DATABASE_URL":"postgresql://test:synthetic-password@127.0.0.1:55433/synthetic_demo",
                    "NEO4J_URI":"bolt://127.0.0.1:57688","NEO4J_PASSWORD":"synthetic-password"}
            path.write_text(json.dumps(config))
            self.assertEqual(load_runtime(path,ports),config)
            with self.assertRaises(StackError):load_runtime(path,(55432,57474,57687))
            config["NEO4J_URI"]="bolt://remote.invalid:57688"
            path.write_text(json.dumps(config))
            with self.assertRaises(StackError):load_runtime(path,ports)

    def test_external_wildcard_and_alias_origins_are_rejected(self):
        for host in ("0.0.0.0","remote.invalid","127.0.0.2","http://localhost","localhost:8872"):
            with self.subTest(host=host),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                self.args("--origin-host",host)
            self.assertEqual(error.exception.code,2)

    def test_conflicting_privileged_or_out_of_range_ports_are_rejected(self):
        for values in (("--postgres-port","80"),("--neo4j-bolt-port","65536"),
                       ("--neo4j-http-port","55432"),("--port","55432"),("--port","0")):
            with self.subTest(values=values),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                self.args(*values)
            self.assertEqual(error.exception.code,2)


if __name__=="__main__":unittest.main()
