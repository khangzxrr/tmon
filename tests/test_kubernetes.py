"""Kubernetes (kubectl JSON) and Podman: parsing, collecting with stub commands, the Services panel and problems."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from tmon import collect, config, demo, render

NOW = time.time()


def iso(seconds_ago):
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pod(ns, name, phase="Running", ready=True, waiting=None, init_waiting=None, age=3600, labels=None, reason=None,
        owner=None):
    state = {"waiting": {"reason": waiting}} if waiting else {"running": {"startedAt": iso(age)}}
    status = {"phase": phase, "startTime": iso(age),
              "containerStatuses": [{"name": "main", "ready": ready, "restartCount": 0, "state": state}]}
    if init_waiting:
        status["initContainerStatuses"] = [{"name": "init", "ready": False, "state": {"waiting": {"reason": init_waiting}}}]
    if reason:
        status["reason"] = reason
    meta = {"namespace": ns, "name": name, "labels": labels or {}}
    if owner:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "name": owner}]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": meta, "status": status}


PODS = {"apiVersion": "v1", "kind": "List", "items": [
    pod("kube-system", "coredns-7b98449c4-x2kfj", labels={"k8s-app": "kube-dns"}),
    pod("kube-system", "traefik-5d45fc8cc9-h8q2m", labels={"app.kubernetes.io/name": "traefik"}),
    pod("kube-system", "helm-install-traefik-crd-ljx7w", phase="Succeeded", ready=False),
    pod("cloud", "nextcloud-6f9c7d8b5-abcde", ready=False, waiting="CrashLoopBackOff", labels={"app": "nextcloud"}),
    pod("cloud", "redis-0", owner="redis"),
    pod("photos", "immich-server-5c7b-qwert", phase="Pending", ready=False, init_waiting="ImagePullBackOff"),
    pod("photos", "immich-ml-7d9f-zxcvb", phase="Pending", ready=False, waiting="ContainerCreating", age=30),
    pod("photos", "immich-db-0", ready=False, age=900),
    pod("backup", "nightly-28771230-abcde", phase="Failed", ready=False, reason="BackoffLimitExceeded"),
    pod("backup", "old-evicted-pod", phase="Failed", ready=False, reason="Evicted"),
]}
NODES = {"items": [
    {"metadata": {"name": "nas"}, "status": {"conditions": [{"type": "MemoryPressure", "status": "False"},
                                                            {"type": "Ready", "status": "True"}]}},
    {"metadata": {"name": "pi"}, "status": {"conditions": [{"type": "Ready", "status": "Unknown"}]}},
]}
PODMAN = [
    {"Id": "a1b2c3d4e5f6a7b8", "Names": ["web_app_1"], "State": "running", "Status": "Up 2 hours (healthy)",
     "Labels": {"com.docker.compose.project": "web"}},
    {"Id": "b1b2c3d4e5f6a7b8", "Names": ["web_db_1"], "State": "exited", "Status": "Exited (1) 3 minutes ago",
     "Labels": {"io.podman.compose.project": "web"}},
    {"Id": "c1b2c3d4e5f6a7b8", "Names": None, "State": "running", "Status": "", "Labels": None},
]


class ParseTest(unittest.TestCase):
    def test_pods(self):
        rows = {r[1]: r for r in collect.parse_pods(PODS, now=NOW)}
        self.assertEqual(rows["kube-system/coredns-7b98449c4-x2kfj"], ["kube-system", "kube-system/coredns-7b98449c4-x2kfj",
                                                                       "running", "Up"])
        self.assertNotIn("kube-system/helm-install-traefik-crd-ljx7w", rows)  # finished job
        self.assertNotIn("backup/old-evicted-pod", rows)
        self.assertEqual(rows["cloud/nextcloud-6f9c7d8b5-abcde"][2:], ["broken", "CrashLoopBackOff"])
        self.assertEqual(rows["photos/immich-server-5c7b-qwert"][2:], ["broken", "ImagePullBackOff"])
        self.assertEqual(rows["photos/immich-ml-7d9f-zxcvb"][2:], ["running", "starting"])
        self.assertEqual(rows["photos/immich-db-0"][2:], ["broken", "not ready"])
        self.assertEqual(rows["backup/nightly-28771230-abcde"][2:], ["broken", "BackoffLimitExceeded"])

    def test_group_by_app(self):
        groups = {r[1]: r[0] for r in collect.parse_pods(PODS, "app", now=NOW)}
        self.assertEqual(groups["kube-system/coredns-7b98449c4-x2kfj"], "kube-dns")
        self.assertEqual(groups["kube-system/traefik-5d45fc8cc9-h8q2m"], "traefik")
        self.assertEqual(groups["cloud/nextcloud-6f9c7d8b5-abcde"], "nextcloud")
        self.assertEqual(groups["cloud/redis-0"], "redis")  # owner
        self.assertEqual(groups["photos/immich-db-0"], "immich-db-0")  # nothing else to go by

    def test_edge_cases(self):
        self.assertIsNone(collect.k8s_time(None))
        self.assertIsNone(collect.k8s_time("yesterday"))
        self.assertEqual(collect.pod_state({"status": {"phase": "Pending"}}), ("running", "starting"))
        self.assertEqual(collect.pod_state({"status": {"phase": "Pending", "startTime": iso(900)}}, NOW),
                         ("broken", "Pending"))
        self.assertEqual(collect.pod_state({"status": {"phase": "Failed"}}), ("broken", "Failed"))
        self.assertEqual(collect.parse_pods({}), [])
        self.assertEqual(collect.parse_pods({"items": [{}]}), [["?", "?/?", "running", "starting"]])

    def test_nodes(self):
        self.assertEqual(collect.parse_nodes(NODES), [("nas", True), ("pi", False)])
        self.assertEqual(collect.parse_nodes({"items": [{}]}), [("?", False)])

    def test_podman(self):
        self.assertEqual(collect.parse_podman(json.dumps(PODMAN)), [
            ["web", "web_app_1", "running", "Up 2 hours (healthy)"], ["web", "web_db_1", "exited",
                                                                      "Exited (1) 3 minutes ago"],
            ["", "c1b2c3d4e5f6", "running", "running"]])
        self.assertEqual(collect.parse_podman(""), [])
        self.assertIsNone(collect.parse_podman("not json"))


class KubectlCommandTest(unittest.TestCase):
    def k(self, **kw):
        return dict(config.DEFAULTS["kubernetes"], **kw)

    def test_command(self):
        with mock.patch.object(config, "local_cluster", return_value="k3s"):
            self.assertEqual(collect.kubectl(self.k())[:2], ["k3s", "kubectl"])
        with mock.patch.object(config, "local_cluster", return_value="kubeadm"), \
                mock.patch.dict(os.environ) as env:
            env.pop("KUBECONFIG", None)
            self.assertEqual(collect.kubectl(self.k()),
                             ["kubectl", "--kubeconfig", "/etc/kubernetes/admin.conf", "--request-timeout=20s"])
            env["KUBECONFIG"] = "/root/.kube/config"
            self.assertEqual(collect.kubectl(self.k()), ["kubectl", "--request-timeout=20s"])
        with mock.patch.object(config, "local_cluster", return_value=None):
            self.assertEqual(collect.kubectl(self.k(command="microk8s kubectl", kubeconfig="/x")),
                             ["microk8s", "kubectl", "--kubeconfig", "/x", "--request-timeout=20s"])


class ConfigTest(unittest.TestCase):
    def test_auto_only_for_a_local_cluster(self):
        with mock.patch("shutil.which", side_effect=lambda c: "/usr/local/bin/k3s" if c == "k3s" else None), \
                mock.patch("os.path.exists", side_effect=lambda p: p == config.K3S_KUBECONFIG):
            self.assertEqual(config.local_cluster(), "k3s")
            self.assertTrue(config.merge({})["kubernetes"]["enabled"])
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("os.path.exists", side_effect=lambda p: p == config.KUBEADM_KUBECONFIG):
            self.assertEqual(config.local_cluster(), "kubeadm")
        with mock.patch("shutil.which", side_effect=lambda c: "/usr/bin/kubectl" if c == "kubectl" else None), \
                mock.patch("os.path.exists", return_value=False):
            self.assertIsNone(config.local_cluster())  # kubectl alone may point at somebody else's cluster
            self.assertFalse(config.merge({})["kubernetes"]["enabled"])

    def test_podman_auto_and_validation(self):
        with mock.patch("shutil.which", side_effect=lambda c: "/usr/bin/podman" if c == "podman" else None):
            self.assertTrue(config.merge({"docker": {"command": "podman"}})["docker"]["enabled"])
        for bad in ({"docker": {"command": "nerdctl"}}, {"kubernetes": {"enabled": "yes"}},
                    {"kubernetes": {"group_by": "label"}}):
            with self.assertRaises(config.ConfigError):
                config.merge(bad)
        self.assertIn("services", config.panels(config.merge({"docker": {"enabled": False},
                                                              "kubernetes": {"enabled": True}})))


class CollectTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, out in (("kubectl", None), ("podman", json.dumps(PODMAN))):
            path = os.path.join(d.name, name)
            with open(path, "w") as f:
                if name == "kubectl":
                    f.write(f"""#!/bin/sh
[ -n "$KUBE_FAIL" ] && {{ echo 'The connection to the server was refused' >&2; exit 1; }}
case "$*" in
  *pods*) cat <<'EOF'\n{json.dumps(PODS)}\nEOF
  ;;
  *nodes*) cat <<'EOF'\n{json.dumps(NODES)}\nEOF
  ;;
esac
""")
                else:
                    f.write(f"#!/bin/sh\n[ -n \"$PODMAN_FAIL\" ] && exit 125\ncat <<'EOF'\n{out}\nEOF\n")
            os.chmod(path, 0o755)
        for p in (mock.patch.dict(os.environ, {"PATH": d.name + os.pathsep + os.environ["PATH"]}),
                  mock.patch.object(config, "local_cluster", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.cfg = config.merge({"docker": {"enabled": True, "command": "podman"},
                                 "kubernetes": {"enabled": True, "group_by": "namespace"}})

    def test_collect(self):
        c = collect.Collector(self.cfg)
        c.run_all()
        self.assertEqual(len(c.data["k8s"]["pods"]), 8)
        self.assertEqual(c.data["k8s"]["nodes"], [("nas", True), ("pi", False)])
        self.assertEqual(c.data["containers"][0][:2], ["web", "web_app_1"])
        with mock.patch.dict(os.environ, {"KUBE_FAIL": "1", "PODMAN_FAIL": "1"}):
            c.run_all()
        self.assertIsNone(c.data["k8s"])
        self.assertIsNone(c.data["containers"])


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.merge(dict(demo.CONFIG, kubernetes={"enabled": True},
                                     docker=dict(demo.CONFIG["docker"], enabled=True)))
        self.s = demo.DemoSource(demo.config_()).snapshot()
        self.s["health_enabled"] = False

    def screen(self):
        return render.ANSI.sub("", "\n".join(render.build(120, 45, self.cfg, self.s, datetime.now())))

    def test_healthy_cluster(self):
        self.s["slow"]["k8s"] = {"pods": [["kube-system", "kube-system/coredns", "running", "Up"],
                                          ["cloud", "cloud/nextcloud", "running", "Up"],
                                          ["photos", "photos/immich", "running", "starting"]],
                                 "nodes": [("nas", True)]}
        out = self.screen()
        self.assertIn("Kubernetes ■ nodes 1/1 Ready · 2/3 pods ready", out)
        self.assertIn("■ cloud       ■ kube-system ■ photos", out)
        self.assertIn("ALL SYSTEMS OK", out)

    def test_broken_cluster(self):
        self.s["slow"]["k8s"] = {"pods": collect.parse_pods(PODS, now=NOW), "nodes": collect.parse_nodes(NODES)}
        self.cfg["kubernetes"]["groups"] = {"kube-system": "System", "cloud": "Cloud", "media": "Media"}
        out = self.screen()
        self.assertIn("Kubernetes ■ nodes 1/2 Ready · 3/8 pods ready", out)
        self.assertIn("■ System ■ Cloud  ■ Media", out)
        for problem in ("node pi NotReady", "pod cloud/nextcloud-6f9c7d8b5-abcde: CrashLoopBackOff",
                        "pod photos/immich-db-0: not ready", "Media: no pods"):
            self.assertIn(problem, out)

    def test_kubectl_down_and_no_data_yet(self):
        self.s["slow"]["k8s"] = None
        out = self.screen()
        self.assertIn("Kubernetes ! kubectl not answering", out)
        self.assertIn("kubernetes: kubectl not answering", out)
        del self.s["slow"]["k8s"]
        self.assertIn("Kubernetes no data", self.screen())

    def test_kubernetes_only_and_podman(self):
        self.cfg["docker"]["enabled"] = False
        self.s["slow"]["k8s"] = {"pods": [], "nodes": [("nas", True)]}
        out = self.screen()
        self.assertIn(" SERVICES\n", out.replace(" " * 20, "\n"))
        self.assertIn("nodes 1/1 Ready · 0/0 pods ready", out)
        self.cfg["docker"].update(enabled=True, command="podman")
        self.s["slow"]["containers"] = None
        out = self.screen()
        self.assertIn("Podman     ! podman not answering", out)
        self.assertIn("! podman not answering", out)


if __name__ == "__main__":
    unittest.main()
