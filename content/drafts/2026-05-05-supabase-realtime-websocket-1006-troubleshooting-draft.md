---
title: "Supabase Realtime WebSocket 1006 Abnormal Closure on EC2"
date: 2026-05-05
draft: true
categories: ["troubleshooting"]
tags: ["ec2", "supabase", "websocket", "realtime", "aws", "nat"]
---

## 문제 상황

EC2에서 실행 중인 Python AI 워커 로그에 아래 에러가 주기적으로 발생했다.

```
realtime._async.client - ERROR - Websocket connection closed with code: 1006, reason:
```

1006이면서 reason이 비어있는 것이 특징. 앱 코드에서 명시적으로 연결을 닫은 게 아닌데 끊어지고 있었다.

## 원인 분석

EC2에서 외부 서버(Supabase Realtime)로 WebSocket을 맺는 흐름:

```
EC2 → NAT Gateway → Internet → Supabase Realtime
```

NAT Gateway는 연결을 **Connection Tracking Table**에 기록해두는데, idle 상태가 지속되면 해당 항목을 삭제한다. AWS NAT Gateway의 idle timeout은 **350초(약 5분 50초)**.

NAT가 연결 기록을 지워도 EC2와 Supabase는 이 사실을 모른다. 이후 Supabase에서 heartbeat나 이벤트 패킷이 오면 NAT가 처리하지 못하고 패킷을 드랍하면서 연결이 비정상 종료된다. 정상적인 종료 handshake 없이 끊기므로 reason이 비어있는 것.

`realtime.timeout = 30` 설정은 초기 연결 타임아웃이고, NAT idle timeout과는 무관하다.

## 동작 확인

WebSocket이 끊긴 이후에도 "놓친 감정 확인 중..." 로그가 뜨며 Supabase로 HTTP GET 요청이 나가고 있었다. 이는 `periodic_check`가 별도의 HTTP polling으로 Supabase REST API를 주기적으로 조회하는 것으로, WebSocket Realtime과는 완전히 별개 경로다.

| 경로 | 방식 | 상태 |
|------|------|------|
| Realtime 구독 | WebSocket (push) | 끊어짐 |
| periodic_check | HTTP polling (pull) | 정상 동작 |

즉, 완전히 죽은 건 아니고 polling이 fallback 역할을 하고 있었다. 다만 실시간 처리가 안 되고 polling 주기마다만 이벤트를 처리하는 degraded 상태.

## 해결

`realtime-py` 라이브러리에 `auto_reconnect=True`(기본값)가 있어서 WebSocket 자체는 재연결을 시도한다. 그런데 1006으로 끊기면 채널 state가 `ERRORED`로 전환되고, 라이브러리의 `_reconnect()`는 `JOINED`/`JOINING` 상태인 채널만 재구독하므로 채널이 복구되지 않는다.

결국 WebSocket은 살아있는데 이벤트를 못 받는 상태가 된다.

**watchdog 추가로 해결**

60초마다 `is_connected`를 확인하고, 끊겼으면 기존 채널을 정리(`remove_all_channels`)하고 새로 구독하는 방식으로 처리했다.

```python
async def realtime_watchdog(supabase, pipeline):
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(60)
        if not supabase.realtime.is_connected:
            logger.warning("⚠️ Realtime 연결 끊김 감지. 재연결 시도...")
            for attempt in range(3):
                try:
                    await supabase.realtime.remove_all_channels()
                    await subscribe_channels(supabase, pipeline)
                    logger.info("✅ Realtime 재연결 성공")
                    break
                except Exception as e:
                    logger.error(f"재연결 실패 (시도 {attempt + 1}/3): {e}")
                    if attempt < 2:
                        await asyncio.sleep(5)
            else:
                logger.error("❌ Realtime 재연결 최종 실패.")
```

`remove_all_channels`는 ERRORED 상태로 남은 채널 딕셔너리를 비워서 같은 이름으로 새 채널을 등록할 수 있게 한다.

재구독 사이 짧은 공백이 생기지만, `periodic_check`가 5분마다 미처리 항목을 HTTP로 보정하므로 유실 없이 처리된다.
