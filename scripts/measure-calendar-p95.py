#!/usr/bin/env python3
"""Measure authenticated calendar API latency without printing credentials."""
import argparse
import statistics
import time
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument("--url",default="http://127.0.0.1:8080/api/v1/calendar?month=2026-07")
parser.add_argument("--cookie",required=True,help="session cookie header value; never printed")
parser.add_argument("--requests",type=int,default=50)
parser.add_argument("--target-ms",type=float,default=300.0)
args=parser.parse_args()
if not 5<=args.requests<=1000: parser.error("--requests must be between 5 and 1000")
samples=[]
for _ in range(args.requests):
    request=urllib.request.Request(args.url,headers={"Cookie":args.cookie})
    started=time.perf_counter()
    with urllib.request.urlopen(request,timeout=10) as response:
        if response.status!=200: raise SystemExit(f"unexpected HTTP status: {response.status}")
        response.read()
    samples.append((time.perf_counter()-started)*1000)
samples.sort(); p95=samples[max(0,int(len(samples)*.95)-1)]
print(f"requests={len(samples)} median_ms={statistics.median(samples):.1f} p95_ms={p95:.1f} target_ms={args.target_ms:.1f}")
raise SystemExit(0 if p95<=args.target_ms else 1)
