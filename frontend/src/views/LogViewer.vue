<template>
  <div class="log-viewer">
    <div ref="logContainer" class="log-container">
      <div v-for="(log, index) in logs" :key="index" class="log-line">
        {{ log }}
      </div>
    </div>
    <div style="margin-top: 10px">
      <a-space>
        <a-button type="primary" @click="clearLogs">清空日志</a-button>
        <a-tag v-if="connected" color="success">已连接</a-tag>
        <a-tag v-else color="error">已断开</a-tag>
      </a-space>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';

const logs = ref<string[]>([]);
const logContainer = ref<HTMLElement | null>(null);
const connected = ref(false);
let ws: WebSocket | null = null;

const connect = () => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Use development port or relative path
  const host = window.location.port === '5173' ? 'localhost:8000' : window.location.host;
  ws = new WebSocket(`${protocol}//${host}/ws/logs`);

  ws.onopen = () => {
    connected.value = true;
    logs.value.push("--- 已建立日志连接 ---");
  };

  ws.onmessage = (event) => {
    logs.value.push(event.data);
    if (logs.value.length > 500) logs.value.shift();
    scrollToBottom();
  };

  ws.onclose = () => {
    connected.value = false;
    logs.value.push("--- 日志连接已断开，正在尝试重连... ---");
    setTimeout(connect, 3000);
  };
};

const scrollToBottom = async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
};

const clearLogs = () => {
  logs.value = [];
};

onMounted(() => {
  connect();
});

onUnmounted(() => {
  if (ws) ws.close();
});
</script>

<style scoped>
.log-container {
  height: 60vh;
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 10px;
  overflow-y: auto;
  font-family: 'Courier New', Courier, monospace;
  font-size: 13px;
  border-radius: 4px;
}
.log-line {
  margin-bottom: 2px;
  white-space: pre-wrap;
}
</style>
