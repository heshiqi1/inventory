# -*- coding: utf-8 -*-
"""请求 task-node 3014 接口"""

import requests
import json

url = "https://pi-cn.xtalpi.xyz/xtalcase.Production/api/task-node/3014/"

payload = {
    "input": {
        "cost_dimension": {
            "case_name": "XPF2602",
            "service_type": "CSP",
            "unit_name": "Z1",
            "case_detail": "XPF2602-CSP-Z1",
            "unit_id": "1449"
        },
        "limit": 70
    }
}

headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IlEwSTFNemsyTnpWQlJEbENPRUZETTBGQk16WXpSVGcxTURWRU5qQkZNa0pCTWpNMk5UTkVSQSJ9.eyJodHRwczovL2F1dGgueHRhbHBpLmNvbS9NZXRhZGF0YSI6eyJhcHBfbWV0YWRhdGEiOnsiYXV0aG9yaXphdGlvbiI6eyJncm91cHMiOltdLCJyb2xlcyI6W119fSwidXNlclByaW5jaXBhbE5hbWUiOiJjYXNlLnRlc3QyQHh0YWxwaS5jb20iLCJ1c2VyX21ldGFkYXRhIjp7ImF2YXRhciI6InVzZXItYWR8TE9DQUwtQUR8ZDEzYzFkNmYtNmRmYi00YTYxLWFiM2UtNDA2ZGJlOWZlNWZlLzIwMTktMDEvYXV0aDAtZGVmYXVsdC1hdmF0YXItNzQucG5nIiwibmlja25hbWUiOiJjYXNlLnRlc3QyIn19LCJpc3MiOiJodHRwczovL3h0YWxwaS1hdXRoLmF1dGgwLmNvbS8iLCJzdWIiOiJhZHxMT0NBTC1BRHxlODNiY2RmNS0xNGY3LTQ0ZWItOTJiMS03OTY5ZjNlY2M2ZmMiLCJhdWQiOlsieHRhbHBpLXN6IiwiaHR0cHM6Ly94dGFscGktYXV0aC5hdXRoMC5jb20vdXNlcmluZm8iXSwiaWF0IjoxNzcyNjQ5MzI4LCJleHAiOjE3NzI3MzU3MjgsInNjb3BlIjoib3BlbmlkIHByb2ZpbGUiLCJndHkiOiJwYXNzd29yZCIsImF6cCI6ImFhNnMxWFo3bk1yejM2WXdubmFQdFV6R01qTElEbDRPIn0.mjOCW1uSEmtwJBW3i0u03OlbpSRf2c5VmYBgQRU6FcKeTcUtKvEtpzEKxldC78nlOD_m2FEFOG6RCH6OtUbpBDRSoLPRAzlzO6wlBv2osI8n64weoSd2U3-31WY24mL8es247mH6BzwOfhwiL0QR4j_vZMuiJEZyIMQrqZo189aZZgdb8H-9kBsOH9PvBWi7IPcquM63kgYrPzA-gXCe5mOIznJIgPPw-geOhdYGUV1bn-MxWZgzxXx-mQBpEKfso7IwPN36pEQkmkg8EJ7yrkcg3IaCYldfIp5VaklaRMw_CUfk4EmhWpKuGADOKXbhWjHpC7H94A4F3h_PZ2V2rg"
}

def main():
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        print("状态码:", resp.status_code)
        print("响应:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data
    except requests.exceptions.RequestException as e:
        print("请求异常:", e)
        if hasattr(e, "response") and e.response is not None:
            print("响应状态码:", e.response.status_code)
            try:
                print("响应内容:", e.response.text)
            except Exception:
                pass
        raise
    except json.JSONDecodeError as e:
        print("JSON 解析异常:", e)
        print("原始响应:", resp.text[:500] if resp else "")
        raise

if __name__ == "__main__":
    main()
