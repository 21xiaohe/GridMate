#!/usr/bin/env bash
# tools 验收演示:三条验收标准。需 gridmate-mysql 容器 + make seed + make dev 就绪。
set -euo pipefail
BASE=http://localhost:8000

echo "== 验收1:工单报修(应选中 query_repair 并按返回结果回答)=="
curl -s $BASE/api/agent -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"工单 1001 的报修到哪了"}' | python3 -m json.tool

echo ""
echo "== 验收2:退费政策(query_faq 命中)=="
curl -s $BASE/api/agent -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"退费政策是什么"}' | python3 -m json.tool

echo ""
echo "== 验收3:违约金是多少(语义鸿沟:答案在「违约金怎么算」的 answer,但 keyword「违约金」LIKE question 查不到 → 漏召回=预期,留 rag 向量检索)=="
curl -s $BASE/api/agent -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","message":"违约金是多少"}' | python3 -m json.tool
