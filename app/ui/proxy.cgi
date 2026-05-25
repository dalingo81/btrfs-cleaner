#!/bin/bash
# BtrFS Cleaner - CGI 代理到本地 Flask 服务
# 基于飞牛论坛攻略：https://club.fnnas.com/forum.php?mod=viewthread&tid=59220
# 将 /cgi/ThirdParty/btrfs-cleaner/proxy.cgi/<path> 转发到 http://localhost:5100/<path>

cgi_name="proxy.cgi"
target_url="http://localhost:5100"

if [[ "$REQUEST_URI" == *"$cgi_name"* ]]; then
    after_proxy="${REQUEST_URI#*$cgi_name}"
    if [[ "$after_proxy" == *"?"* ]]; then
        target_path=$(echo "$after_proxy" | cut -d'?' -f1)
        target_query=$(echo "$after_proxy" | cut -d'?' -f2-)
    else
        target_path="$after_proxy"
        target_query=""
    fi
else
    after_proxy=""
    target_path=""
    target_query="$QUERY_STRING"
fi

if [ -z "$target_path" ]; then
    target_path="/"
fi

target_url="$target_url$target_path"
if [ -n "$target_query" ]; then
    target_url="$target_url?$target_query"
fi

curl_args=(-s --include -X "$REQUEST_METHOD")
if [ -n "$HTTP_COOKIE" ]; then
    curl_args+=(-H "Cookie: $HTTP_COOKIE")
fi
if [ -n "$CONTENT_TYPE" ]; then
    curl_args+=(-H "Content-Type: $CONTENT_TYPE")
fi
curl_args+=("$target_url")

# 去掉 HTTP 状态行（trim_http_cgi 不接受 HTTP/1.1 200 OK 格式）
# 去掉 100 Continue 响应块
if [ "$REQUEST_METHOD" = "POST" ] || [ "$REQUEST_METHOD" = "PUT" ] || [ "$REQUEST_METHOD" = "PATCH" ] || [ "$REQUEST_METHOD" = "DELETE" ]; then
    exec cat | curl "${curl_args[@]}" --data-binary @- | sed -e '1{/^HTTP\//d}' -e '/^HTTP\/1.1 100/,/^\r\?$/d'
else
    exec curl "${curl_args[@]}" | sed -e '1{/^HTTP\//d}' -e '/^HTTP\/1.1 100/,/^\r\?$/d'
fi
