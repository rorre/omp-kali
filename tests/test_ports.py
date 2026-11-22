import unittest

from omp_kali.errors import OmpKaliError
from omp_kali.ports import (
    DEFAULT_BIND,
    PortMapping,
    desired_publish_specs,
    find_conflict,
    relay_payloads,
)


class PortMappingTests(unittest.TestCase):
    def test_ids_and_specs(self) -> None:
        publish = PortMapping("container-to-host", 8080, 80)
        relay = PortMapping("host-to-container", 5432, 5432)
        self.assertEqual(publish.id, "c2h:127.0.0.1:8080:80")
        self.assertEqual(relay.id, "h2c:127.0.0.1:5432:5432")
        self.assertEqual(publish.publish_spec(), "127.0.0.1:8080:80")
        self.assertIsNone(relay.publish_spec())
        self.assertEqual(desired_publish_specs([publish, relay]), ["127.0.0.1:8080:80"])

    def test_relay_payload(self) -> None:
        relay = PortMapping("host-to-container", 5432, 15432, bind="0.0.0.0", target_host="host.containers.internal")
        self.assertEqual(
            relay_payloads([relay]),
            [
                {
                    "id": "h2c:0.0.0.0:5432:15432",
                    "listen_host": "0.0.0.0",
                    "container": 15432,
                    "target_host": "host.containers.internal",
                    "target_port": 5432,
                }
            ],
        )

    def test_invalid_ports(self) -> None:
        with self.assertRaises(OmpKaliError):
            PortMapping("container-to-host", 0, 80)
        with self.assertRaises(OmpKaliError):
            PortMapping("container-to-host", 8080, 70000)
        with self.assertRaises(OmpKaliError):
            PortMapping("container-to-host", 8080, 80, bind="not-an-ip")

    def test_conflicts(self) -> None:
        publish = PortMapping("container-to-host", 8080, 80)
        self.assertIsNotNone(find_conflict([publish], PortMapping("container-to-host", 8080, 81)))
        self.assertIsNone(find_conflict([publish], PortMapping("container-to-host", 8081, 80)))
        relay = PortMapping("host-to-container", 5432, 5432)
        self.assertIsNotNone(find_conflict([relay], PortMapping("host-to-container", 6000, 5432)))

    def test_default_bind(self) -> None:
        self.assertEqual(PortMapping("container-to-host", 8080, 80).bind, DEFAULT_BIND)


if __name__ == "__main__":
    unittest.main()
