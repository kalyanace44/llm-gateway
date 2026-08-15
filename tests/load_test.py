"""
Load test for LLM Gateway.
Usage:
    python tests/load_test.py --url http://localhost:8080 --key test-key-123 --concurrent 10 --requests 50
"""
import argparse
import asyncio
import statistics
import time

import httpx

PROMPTS = [
    "Explain what a load balancer does in one sentence.",
    "What is the CAP theorem?",
    "How does consistent hashing work?",
    "Explain eventual consistency.",
    "What is a message queue used for?",
    "How does rate limiting protect APIs?",
    "What is the difference between SQL and NoSQL?",
    "Explain circuit breaker pattern.",
    "What is a CDN?",
    "How does TLS work?",
]


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    model: str,
    prompt_idx: int,
) -> dict:
    """Make a single chat completion request and return metrics."""
    prompt = PROMPTS[prompt_idx % len(PROMPTS)]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 50,
    }

    start = time.time()
    try:
        resp = await client.post(
            f"{url}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        latency = time.time() - start

        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {
                "status": "success",
                "latency": latency,
                "tokens": tokens,
                "status_code": 200,
                "cached": latency < 0.05,  # Cache hits are near-instant
            }
        else:
            return {
                "status": "error",
                "latency": latency,
                "tokens": 0,
                "status_code": resp.status_code,
                "cached": False,
            }
    except (httpx.HTTPError, TimeoutError, OSError) as e:
        return {
            "status": "error",
            "latency": time.time() - start,
            "tokens": 0,
            "status_code": 0,
            "error": str(e),
            "cached": False,
        }


async def run_load_test(
    url: str,
    api_key: str,
    model: str,
    concurrent: int,
    total_requests: int,
):
    """Run the load test."""
    print(f"\n{'='*60}")
    print("  LLM Gateway Load Test")
    print(f"{'='*60}")
    print(f"  URL:         {url}")
    print(f"  Model:       {model}")
    print(f"  Concurrent:  {concurrent}")
    print(f"  Requests:    {total_requests}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Warm up
        print("  Warming up (2 requests)...")
        for i in range(2):
            await make_request(client, url, api_key, model, i)

        # Run load test
        print(f"  Running {total_requests} requests with {concurrent} concurrency...\n")
        start = time.time()
        semaphore = asyncio.Semaphore(concurrent)
        results = []

        async def bounded_request(idx):
            async with semaphore:
                return await make_request(client, url, api_key, model, idx)

        tasks = [bounded_request(i) for i in range(total_requests)]
        results = await asyncio.gather(*tasks)

        total_time = time.time() - start

    # Analyze results
    successes = [r for r in results if r["status"] == "success"]
    errors = [r for r in results if r["status"] == "error"]
    cached = [r for r in results if r.get("cached")]
    latencies = [r["latency"] for r in successes]
    total_tokens = sum(r["tokens"] for r in successes)

    print(f"  {'='*60}")
    print("  RESULTS")
    print(f"  {'='*60}")
    print(f"  Total time:     {total_time:.2f}s")
    print(f"  Throughput:     {len(results)/total_time:.1f} req/s")
    print(f"  Success:        {len(successes)}/{len(results)} ({len(successes)/len(results)*100:.0f}%)")
    print(f"  Errors:         {len(errors)}")
    print(f"  Cache hits:     {len(cached)}")
    print(f"  Total tokens:   {total_tokens:,}")
    print()

    if latencies:
        print("  Latency (success only):")
        print(f"    p50:  {statistics.median(latencies):.3f}s")
        print(f"    p95:  {sorted(latencies)[int(len(latencies)*0.95)]:.3f}s")
        print(f"    p99:  {sorted(latencies)[int(len(latencies)*0.99)]:.3f}s")
        print(f"    mean: {statistics.mean(latencies):.3f}s")
        print(f"    min:  {min(latencies):.3f}s")
        print(f"    max:  {max(latencies):.3f}s")

    if total_tokens and total_time:
        print(f"\n  Tokens/sec:     {total_tokens/total_time:.0f}")

    print(f"  {'='*60}\n")

    # Error breakdown
    if errors:
        print("  Errors:")
        status_counts = {}
        for e in errors:
            code = e.get("status_code", "unknown")
            status_counts[code] = status_counts.get(code, 0) + 1
        for code, count in sorted(status_counts.items()):
            print(f"    HTTP {code}: {count}")
        print()

    return {
        "total_time": total_time,
        "throughput_rps": len(results) / total_time,
        "success_rate": len(successes) / len(results),
        "p50_latency": statistics.median(latencies) if latencies else 0,
        "p95_latency": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
        "total_tokens": total_tokens,
        "cache_hits": len(cached),
    }


def main():
    parser = argparse.ArgumentParser(description="LLM Gateway Load Test")
    parser.add_argument("--url", default="http://localhost:8080", help="Gateway URL")
    parser.add_argument("--key", default="test-key-123", help="API key")
    parser.add_argument("--model", default="claude-sonnet-4.5", help="Model to test")
    parser.add_argument("--concurrent", type=int, default=5, help="Concurrent requests")
    parser.add_argument("--requests", type=int, default=20, help="Total requests")
    args = parser.parse_args()

    asyncio.run(run_load_test(
        url=args.url,
        api_key=args.key,
        model=args.model,
        concurrent=args.concurrent,
        total_requests=args.requests,
    ))


if __name__ == "__main__":
    main()
