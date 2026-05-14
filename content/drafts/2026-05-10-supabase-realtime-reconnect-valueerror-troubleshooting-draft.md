---
title: "Supabase Realtime 재연결 시 ValueError: Set of Tasks/Futures is empty"
date: 2026-05-10
draft: true
categories: ["troubleshooting"]
tags: ["ec2", "supabase", "websocket", "realtime", "python", "asyncio"]
---

## 문제 상황

WebSocket 1006 에러 이후 watchdog이 재연결을 시도해야 하는데, 실제로는 재연결이 이루어지지 않고 5분 주기 `periodic_check` polling이 돌 때만 감정 기록이 처리되는 상황이었다.

EC2 로그 확인 결과 아래 에러가 찍혀 있었다.

```
realtime._async.client - ERROR - WebSocket connection closed with code: 1006, reason:
realtime._async.channel - ERROR - join push timeout for channel realtime:emotion_events
realtime._async.channel - ERROR - join push timeout for channel realtime:feedback_events
realtime._async.client - ERROR - WebSocket connection closed with code: 1006, reason:
asyncio - ERROR - Task exception was never retrieved
ValueError: Set of Tasks/Futures is empty.

Traceback (most recent call last):
  ...
  File "realtime/_async/client.py", line 139, in _reconnect
    await asyncio.wait(rejoins)
ValueError: Set of Tasks/Futures is empty.
```

## 내부 구조 이해

`subscribe_channels()`에서 `emotion_channel.subscribe()`를 호출하면 라이브러리 내부적으로 `connect()`가 실행된다. 이때 두 개의 백그라운드 태스크가 자동으로 생성된다.

```python
# realtime/_async/client.py
self._listen_task = asyncio.create_task(self._listen())
self._heartbeat_task = asyncio.create_task(self._heartbeat())
```

`_heartbeat_task`는 우리 코드와 별개로 라이브러리가 자체적으로 돌리는 태스크다. 일정 간격마다 서버로 heartbeat 메시지를 전송하고, 전송 실패 시 자동으로 재연결을 시도하는 구조다.

1006이 발생했을 때의 호출 체인:

```
_heartbeat()
  → send()                      ← heartbeat 메시지 전송 시도
    → ConnectionClosedError (1006)
      → _on_connect_error()     ← auto_reconnect=True이면
        → _reconnect()
          → asyncio.wait(rejoins)  ← rejoins가 비어있으면 ValueError
```

`_reconnect()` 내부에서 `to_rejoin`을 구성할 때 `JOINED` 또는 `JOINING` 상태인 채널만 골라낸다. rejoin 타임아웃이 나면 채널 state가 `ERRORED`로 전환되어 `to_rejoin`이 빈 리스트가 되고, 이걸 그대로 `asyncio.wait()`에 넘기면 `ValueError`가 발생한다.

```python
# realtime/_async/client.py L127
to_rejoin = [
    chan
    for chan in self.channels.values()
    if chan.state == ChannelStates.JOINED or chan.state == ChannelStates.JOINING
]
# ...
rejoins = [asyncio.Task(chan._rejoin()) for chan in to_rejoin]
await asyncio.wait(rejoins)  # to_rejoin이 비어있으면 ValueError
```

## 원인 분석

에러 발생 순서를 보면:

1. **첫 번째 1006** — NAT Gateway idle timeout으로 WebSocket 끊김
2. **채널 rejoin 시도** — 라이브러리 내부에서 `emotion_events`, `feedback_events` 채널 재연결 시도
3. **join push timeout** — WebSocket이 완전히 복구되지 않은 상태에서 채널 rejoin을 시도하다가 타임아웃
4. **채널 목록 비워짐** — 타임아웃 처리 과정에서 라이브러리 내부 채널 레지스트리가 초기화됨
5. **두 번째 1006** — 불안정한 연결에서 추가 에러 발생
6. **`_reconnect()` 재호출** — 이 시점에는 채널 목록이 비어있음
7. **`asyncio.wait(rejoins)` 에 빈 set 전달** → `ValueError` 발생
8. **하트비트 태스크 크래시** — "Task exception was never retrieved"로 복구 불가 상태

watchdog은 `is_connected`를 60초마다 확인하는데, 하트비트 태스크 자체가 죽어버리면 연결 상태 감지도 제대로 안 될 수 있다.

추가로 `_listen_task`도 동일하게 `ConnectionClosedError` 발생 시 `_on_connect_error`를 호출한다. 두 태스크가 같은 WebSocket 끊김을 동시에 감지하면 `_reconnect()`가 동시에 두 번 호출될 수 있는데, 이를 막는 락이나 플래그가 없다. 이것도 라이브러리 버그다.

```python
# _listen() - realtime/_async/client.py
except websockets.exceptions.ConnectionClosedError as e:
    await self._on_connect_error(e)  # _heartbeat와 동일하게 처리
```

핵심은 **라이브러리 버그** — `_reconnect()`가 `asyncio.wait()`에 빈 set을 넘기는 경우를 처리하지 않고, 동시 호출을 막는 장치도 없다.

## 라이브러리 현황 및 최신 버전 분석

[supabase/realtime-py](https://github.com/supabase/realtime-py) 레포는 **2025년 8월 21일부로 archived** 처리됐다. 마지막 버전은 2.7.0 (2025-07-28). 이후 supabase-py 모노레포로 이전해 2.19.0부터 릴리즈가 재개됐다.

현재 설치 버전: `realtime 2.29.0` / PyPI 최신: `2.30.0`

최신 main 브랜치의 `_reconnect()` 코드는 아래와 같이 변경됐다.

```python
# 최신 버전 _reconnect()
async def _reconnect(self) -> None:
    self._ws_connection = None
    await self.connect()

    if self.is_connected:
        for topic, channel in self.channels.items():
            await channel._rejoin()
```

`asyncio.wait()` 대신 for 루프를 사용해 **빈 채널 목록일 때 ValueError가 발생하지 않게** 됐다. 채널 상태 필터링도 없애 ERRORED 상태 채널도 rejoin 시도한다.

단, **동시 호출 방지 락은 여전히 없다.** `_heartbeat_task`와 `_listen_task`가 동시에 1006을 감지하면 둘 다 `_reconnect()`를 호출하는 상황은 그대로다. 다만 이제 ValueError 크래시 대신 아래 레이스 컨디션이 남는다:

1. Task A: `_ws_connection = None` → `await connect()` → 연결 완료 → `_ws_connection = new_ws`
2. Task A: for loop 진입 → `await channel._rejoin()` ← await 포인트
3. Task B: `_ws_connection = None` ← Task A가 만든 연결을 덮어씀
4. Task B: `await connect()` → 새 WebSocket 재생성

타이밍에 따라 WebSocket이 두 번 만들어질 수 있다. 크래시는 아니지만 불필요한 재연결이 발생할 수 있다.

## 대응 방향

최신 버전(2.30.0)으로 업그레이드하면 오늘 발생한 ValueError 크래시는 재현되지 않는다. 다만 채널 rejoin 실패 시 watchdog이 없으면 이벤트를 영구적으로 못 받는 상태가 될 수 있으므로 **watchdog은 유지해야 한다.**

- ValueError 크래시 → 최신 버전에서 해결
- 동시 호출 레이스 컨디션 → 미해결, 크래시는 아님
- 채널 상태 복구 안전망 → watchdog 유지 필요

## 참고

- 1006 발생 원인(NAT Gateway idle timeout) 및 watchdog 추가 배경은 별도 문서 참고
  → `2026-05-05-supabase-realtime-websocket-1006-troubleshooting-draft.md`
- [Issue #236 · supabase/realtime-py](https://github.com/supabase/realtime-py/issues/236)
