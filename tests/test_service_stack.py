"""The local launcher must never inherit a remote Docker endpoint."""

import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


spec=importlib.util.spec_from_file_location("service_stack",Path(__file__).resolve().parents[1]/"dev/service_stack.py")
stack=importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)


class LocalDockerSelectionTests(unittest.TestCase):
    def test_explicit_unix_socket_and_desktop_symlink(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as listener:
            path=Path(directory)/"docker.sock"
            listener.bind(str(path))
            self.assertEqual(stack.local_docker_command(str(path)),["docker","--host=unix://"+str(path)])
            link=Path(directory)/"default.sock"
            link.symlink_to(path)
            self.assertEqual(stack.local_docker_command(str(link)),["docker","--host=unix://"+str(link)])

    def test_remote_relative_missing_and_regular_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/"not-a-socket"
            file.write_text("not a socket")
            for value in ("tcp://remote.invalid:2375","ssh://remote.invalid","docker.sock",str(file),str(Path(directory)/"missing")):
                with self.subTest(value=value),self.assertRaises(stack.StackError):
                    stack.local_docker_command(value)

    def test_explicit_socket_ignores_remote_selectors_preserving_proxy_and_registry(self):
        command=["docker","--host=unix:///synthetic/local.sock"]
        environment={"DOCKER_HOST":"tcp://remote.invalid:2375","DOCKER_CONTEXT":"remote",
                     "DOCKER_TLS":"1","DOCKER_TLS_VERIFY":"1","DOCKER_CERT_PATH":"/synthetic/certs",
                     "DOCKER_CONFIG":"/synthetic/config","HTTPS_PROXY":"http://synthetic.proxy:8080"}
        with patch.dict(os.environ,environment),patch.object(stack,"DOCKER",command),patch.object(stack.subprocess,"run") as run:
            run.return_value.returncode=0
            run.return_value.stdout="ok"
            self.assertEqual(stack.docker("info"),"ok")
            args,kwargs=run.call_args
            self.assertEqual(args[0],command+["info"])
            self.assertTrue(all(key not in kwargs["env"] for key in stack.SELECTORS))
            self.assertEqual(kwargs["env"]["DOCKER_CONFIG"],environment["DOCKER_CONFIG"])
            self.assertEqual(kwargs["env"]["HTTPS_PROXY"],environment["HTTPS_PROXY"])


if __name__=="__main__":unittest.main()
