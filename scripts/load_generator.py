#!/usr/bin/env python3
"""
Professional Load Generator for Docker Swarm Research
======================================================
Generates configurable HTTP load against the application and
produces detailed results with per-container distribution,
latency percentiles, and throughput metrics.

Modes:
  constant  - Fixed request rate for a duration
  ramp      - Gradually increasing concurrency
  burst     - Send N requests as fast as possible
  stress    - Keep increasing load until failures occur

Usage:
  python load_generator.py --mode burst --requests 500
  python load_generator.py --mode ramp --concurrency 50 --duration 60
  python load_generator.py --mode stress --duration 120

Results are saved to ../results/ automatically.
"""

import argparse
import asyncio
import time
import json
import sys
import os
from datetime import datetime
from collections import defaultdict
from urllib.parse import urljoin

# Use aiohttp if available, otherwise fall back to synchronous
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class LoadGenerator:
    def __init__(self, base_url, mode, total_requests, concurrency, duration, endpoint):
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.total_requests = total_requests
        self.concurrency = concurrency
        self.duration = duration
        self.endpoint = endpoint

        # Results
        self.results = []
        self.errors = 0
        self.success = 0
        self.container_hits = defaultdict(int)
        self.start_time = None
        self.end_time = None

    def _target_url(self):
        return f"{self.base_url}{self.endpoint}"

    # ------------------------------------------------------------------
    # Async implementation (aiohttp)
    # ------------------------------------------------------------------
    async def _send_request_async(self, session, request_id):
        url = self._target_url()
        start = time.perf_counter()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                elapsed = (time.perf_counter() - start) * 1000
                body = await resp.text()
                status = resp.status
                container_id = "unknown"
                try:
                    data = json.loads(body)
                    container_id = data.get("container_id", "unknown")
                except Exception:
                    pass

                self.results.append({"id": request_id, "status": status, "latency_ms": round(elapsed, 2), "container": container_id})
                if 200 <= status < 400:
                    self.success += 1
                    self.container_hits[container_id] += 1
                else:
                    self.errors += 1
                return status
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append({"id": request_id, "status": 0, "latency_ms": round(elapsed, 2), "container": "error", "error": str(e)})
            self.errors += 1
            return 0

    async def _run_burst_async(self):
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            sem = asyncio.Semaphore(self.concurrency)

            async def limited(i):
                async with sem:
                    return await self._send_request_async(session, i)

            tasks = [limited(i) for i in range(self.total_requests)]
            done = 0
            for coro in asyncio.as_completed(tasks):
                await coro
                done += 1
                if done % 50 == 0 or done == self.total_requests:
                    print(f"  Progress: {done}/{self.total_requests}", end="\r")
            print()

    async def _run_ramp_async(self):
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            end_time = time.time() + self.duration
            req_id = 0
            current_concurrency = 1
            step_duration = self.duration / min(self.concurrency, 10)

            while time.time() < end_time:
                step_end = time.time() + step_duration
                sem = asyncio.Semaphore(current_concurrency)
                print(f"  Concurrency: {current_concurrency}")

                tasks = []
                while time.time() < step_end and time.time() < end_time:
                    async def limited(rid):
                        async with sem:
                            return await self._send_request_async(session, rid)
                    tasks.append(limited(req_id))
                    req_id += 1
                    if len(tasks) >= current_concurrency * 5:
                        await asyncio.gather(*tasks)
                        tasks = []
                if tasks:
                    await asyncio.gather(*tasks)

                current_concurrency = min(current_concurrency + max(1, self.concurrency // 10), self.concurrency)

            self.total_requests = req_id

    async def _run_stress_async(self):
        connector = aiohttp.TCPConnector(limit=200)
        async with aiohttp.ClientSession(connector=connector) as session:
            end_time = time.time() + self.duration
            req_id = 0
            concurrency = 5
            prev_error_rate = 0.0

            while time.time() < end_time:
                sem = asyncio.Semaphore(concurrency)
                batch_start = len(self.results)

                tasks = []
                for _ in range(concurrency * 3):
                    if time.time() >= end_time:
                        break
                    async def limited(rid):
                        async with sem:
                            return await self._send_request_async(session, rid)
                    tasks.append(limited(req_id))
                    req_id += 1
                if tasks:
                    await asyncio.gather(*tasks)

                batch = self.results[batch_start:]
                batch_errors = sum(1 for r in batch if r["status"] == 0 or r["status"] >= 500)
                error_rate = batch_errors / max(len(batch), 1) * 100

                print(f"  Concurrency: {concurrency} | Error rate: {error_rate:.1f}%")

                if error_rate > 50:
                    print(f"  ⚠️  Breaking point found at concurrency {concurrency}")
                    break

                concurrency = min(concurrency + 5, 200)

            self.total_requests = req_id

    # ------------------------------------------------------------------
    # Sync fallback (requests library)
    # ------------------------------------------------------------------
    def _send_request_sync(self, request_id):
        url = self._target_url()
        start = time.perf_counter()
        try:
            resp = requests.get(url, timeout=30)
            elapsed = (time.perf_counter() - start) * 1000
            container_id = "unknown"
            try:
                data = resp.json()
                container_id = data.get("container_id", "unknown")
            except Exception:
                pass
            self.results.append({"id": request_id, "status": resp.status_code, "latency_ms": round(elapsed, 2), "container": container_id})
            if 200 <= resp.status_code < 400:
                self.success += 1
                self.container_hits[container_id] += 1
            else:
                self.errors += 1
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self.results.append({"id": request_id, "status": 0, "latency_ms": round(elapsed, 2), "container": "error", "error": str(e)})
            self.errors += 1

    def _run_burst_sync(self):
        for i in range(self.total_requests):
            self._send_request_sync(i)
            if (i + 1) % 50 == 0 or (i + 1) == self.total_requests:
                print(f"  Progress: {i+1}/{self.total_requests}", end="\r")
        print()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self):
        print(f"\n{'='*60}")
        print(f"  LOAD GENERATOR — {self.mode.upper()} MODE")
        print(f"{'='*60}")
        print(f"  Target: {self._target_url()}")
        print(f"  Requests: {self.total_requests}")
        print(f"  Concurrency: {self.concurrency}")
        print(f"  Duration: {self.duration}s")
        print(f"  Engine: {'aiohttp (async)' if HAS_AIOHTTP else 'requests (sync)'}")
        print(f"{'='*60}\n")

        self.start_time = time.time()

        if HAS_AIOHTTP:
            if self.mode == "burst":
                asyncio.run(self._run_burst_async())
            elif self.mode == "ramp":
                asyncio.run(self._run_ramp_async())
            elif self.mode == "stress":
                asyncio.run(self._run_stress_async())
            elif self.mode == "constant":
                asyncio.run(self._run_burst_async())
        else:
            print("  ⚠️  aiohttp not installed — running synchronously")
            self._run_burst_sync()

        self.end_time = time.time()

    def get_report(self):
        total_time = self.end_time - self.start_time
        latencies = sorted([r["latency_ms"] for r in self.results])
        total = len(self.results)

        if not latencies:
            return {"error": "No results"}

        p50 = latencies[int(total * 0.50)] if total > 0 else 0
        p95 = latencies[int(total * 0.95)] if total > 1 else latencies[0]
        p99 = latencies[int(total * 0.99)] if total > 1 else latencies[0]

        # Distribution analysis
        distribution = dict(self.container_hits)
        total_hits = sum(distribution.values())
        distribution_pct = {k: round(v / max(total_hits, 1) * 100, 1) for k, v in distribution.items()}
        ideal_pct = 100 / max(len(distribution), 1)
        max_deviation = max(abs(v - ideal_pct) for v in distribution_pct.values()) if distribution_pct else 0

        report = {
            "mode": self.mode,
            "target_url": self._target_url(),
            "total_requests": total,
            "successful": self.success,
            "failed": self.errors,
            "error_rate_percent": round(self.errors / max(total, 1) * 100, 2),
            "total_time_seconds": round(total_time, 2),
            "requests_per_second": round(total / max(total_time, 0.001), 1),
            "latency": {
                "min_ms": round(latencies[0], 2),
                "max_ms": round(latencies[-1], 2),
                "avg_ms": round(sum(latencies) / total, 2),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
            },
            "container_distribution": distribution,
            "container_distribution_percent": distribution_pct,
            "distribution_deviation_from_ideal": round(max_deviation, 1),
            "containers_used": len(distribution),
            "timestamp": datetime.utcnow().isoformat(),
        }
        return report

    def print_report(self):
        r = self.get_report()
        print(f"\n{'='*60}")
        print(f"  LOAD TEST RESULTS")
        print(f"{'='*60}")
        print(f"  Mode:             {r['mode']}")
        print(f"  Total requests:   {r['total_requests']}")
        print(f"  Successful:       {r['successful']}")
        print(f"  Failed:           {r['failed']} ({r['error_rate_percent']}%)")
        print(f"  Total time:       {r['total_time_seconds']}s")
        print(f"  Throughput:       {r['requests_per_second']} req/s")
        print(f"\n  Latency:")
        lat = r["latency"]
        print(f"    Min:   {lat['min_ms']}ms")
        print(f"    Avg:   {lat['avg_ms']}ms")
        print(f"    P50:   {lat['p50_ms']}ms")
        print(f"    P95:   {lat['p95_ms']}ms")
        print(f"    P99:   {lat['p99_ms']}ms")
        print(f"    Max:   {lat['max_ms']}ms")
        print(f"\n  Container Distribution:")
        for cid, count in r["container_distribution"].items():
            pct = r["container_distribution_percent"].get(cid, 0)
            print(f"    {cid[:12]:>12s}  →  {count:>5d} reqs  ({pct}%)")
        print(f"  Max deviation from ideal: {r['distribution_deviation_from_ideal']}%")
        print(f"{'='*60}\n")
        return r

    def save_results(self, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        r = self.get_report()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"load_test_{self.mode}_{ts}.md"
        filepath = os.path.join(output_dir, filename)

        # Build markdown
        lat = r["latency"]
        lines = [
            f"# Load Test Results — {self.mode.upper()} Mode",
            f"",
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Target**: `{r['target_url']}`",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Requests | {r['total_requests']} |",
            f"| Successful | {r['successful']} |",
            f"| Failed | {r['failed']} ({r['error_rate_percent']}%) |",
            f"| Total Time | {r['total_time_seconds']}s |",
            f"| Throughput | {r['requests_per_second']} req/s |",
            f"| Containers Used | {r['containers_used']} |",
            f"",
            f"## Latency",
            f"",
            f"| Percentile | Latency |",
            f"|------------|---------|",
            f"| Min | {lat['min_ms']}ms |",
            f"| Avg | {lat['avg_ms']}ms |",
            f"| P50 | {lat['p50_ms']}ms |",
            f"| P95 | {lat['p95_ms']}ms |",
            f"| P99 | {lat['p99_ms']}ms |",
            f"| Max | {lat['max_ms']}ms |",
            f"",
            f"## Container Distribution",
            f"",
            f"| Container ID | Requests | Percentage |",
            f"|---|---|---|",
        ]
        for cid, count in sorted(r["container_distribution"].items()):
            pct = r["container_distribution_percent"].get(cid, 0)
            lines.append(f"| `{cid[:12]}` | {count} | {pct}% |")

        lines += [
            f"",
            f"Max deviation from ideal balance: **{r['distribution_deviation_from_ideal']}%**",
            f"",
            f"## Verdict",
            f"",
            f"- Error rate: {'✅ PASS (<5%)' if r['error_rate_percent'] < 5 else '❌ FAIL (>5%)'}",
            f"- Distribution: {'✅ BALANCED (<10% deviation)' if r['distribution_deviation_from_ideal'] < 10 else '⚠️ UNEVEN (>10% deviation)'}",
            f"- Throughput: {r['requests_per_second']} req/s",
        ]

        with open(filepath, "w") as f:
            f.write("\n".join(lines))

        print(f"  📄 Results saved to: {filepath}")
        return filepath


def main():
    parser = argparse.ArgumentParser(description="Load Generator for Docker Swarm Research")
    parser.add_argument("--url", default="http://localhost:8888/api", help="Base URL")
    parser.add_argument("--endpoint", default="/", help="Endpoint to hit")
    parser.add_argument("--mode", choices=["burst", "ramp", "stress", "constant"], default="burst", help="Load mode")
    parser.add_argument("--requests", type=int, default=200, help="Total requests (burst/constant)")
    parser.add_argument("--concurrency", type=int, default=20, help="Max concurrent requests")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds (ramp/stress)")
    parser.add_argument("--output", default=None, help="Output directory for results")
    args = parser.parse_args()

    if not HAS_AIOHTTP and not HAS_REQUESTS:
        print("ERROR: Install 'aiohttp' or 'requests': pip install aiohttp")
        sys.exit(1)

    output_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")

    gen = LoadGenerator(
        base_url=args.url,
        mode=args.mode,
        total_requests=args.requests,
        concurrency=args.concurrency,
        duration=args.duration,
        endpoint=args.endpoint,
    )
    gen.run()
    gen.print_report()
    gen.save_results(output_dir)


if __name__ == "__main__":
    main()
