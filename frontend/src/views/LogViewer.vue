<template>
  <div class="log-viewer-container">
    <!-- Header -->
    <div class="log-header">
      <div class="header-left">
        <span class="title">实时日志</span>
      </div>
      <div class="header-right">
        <a-space :size="12">
          
          <div class="control-buttons">
            <a-tooltip :title="isPaused ? '恢复' : '暂停'">
              <a-button size="small" type="text" @click="togglePause">
                <template #icon>
                  <Pause v-if="!isPaused" :size="16" />
                  <Play v-else :size="16" />
                </template>
              </a-button>
            </a-tooltip>
            
            <a-tooltip title="重连">
              <a-button size="small" type="text" @click="reconnect">
                <template #icon><RefreshCw :size="16" :class="{ 'spinning': isReconnecting }" /></template>
              </a-button>
            </a-tooltip>
            
            <a-tooltip title="清空">
              <a-button size="small" type="text" @click="clearLogs">
                <template #icon><Trash2 :size="16" /></template>
              </a-button>
            </a-tooltip>
            
            <a-tooltip title="下载">
              <a-button size="small" type="text" @click="downloadLogs">
                <template #icon><Download :size="16" /></template>
              </a-button>
            </a-tooltip>
          </div>

          <div class="scroll-switch">
            <a-switch v-model:checked="autoScroll" size="small" @change="toggleScroll" />
            <span class="switch-label">滚动</span>
          </div>
        </a-space>
      </div>
    </div>

    <!-- Log Body -->
    <div ref="logContainer" class="log-body" @scroll="handleScroll">
      <div v-for="(log, index) in filteredLogs" :key="index" class="log-row">
        <div class="log-col time">{{ log.time }}</div>
        <div class="log-col level" :class="log.level.toLowerCase()">{{ log.level }}</div>
        <div class="log-col source">{{ log.source }}</div>
        <div class="log-col message">{{ log.message }}</div>
      </div>
    </div>

    <!-- Footer -->
    <div class="log-footer">
      <div class="footer-left">
        <div class="status-indicator">
          <Terminal :size="14" />
          <span>后端服务</span>
        </div>
      </div>
      <div class="footer-center">
        <span class="log-count">{{ logs.length }} 行</span>
      </div>
      <div class="footer-right">
        <div class="realtime-status">
          <span class="status-dot" :class="{ 'connected': connected && !isPaused, 'paused': isPaused }"></span>
          <span>{{ isPaused ? '已暂停' : '实时' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue';
import { 
  Pause, Play, RefreshCw, Trash2, Download, Terminal 
} from 'lucide-vue-next';
import { message } from 'ant-design-vue';

interface LogEntry {
  raw: string;
  time: string;
  level: string;
  source: string;
  message: string;
}

const logs = ref<LogEntry[]>([]);
const logContainer = ref<HTMLElement | null>(null);
const connected = ref(false);
const isPaused = ref(false);
const autoScroll = ref(true);
const isReconnecting = ref(false);
let ws: WebSocket | null = null;

// Parse log string: "2026-02-09 08:39:10 | DEBUG | app.main:79 | message"
const parseLog = (data: string): LogEntry => {
  const parts = data.split(' | ');
  if (parts.length >= 4) {
    return {
      raw: data,
      time: parts[0] || '-',
      level: parts[1] || 'INFO',
      source: parts[2] || '-',
      message: parts.slice(3).join(' | ')
    };
  } else if (parts.length === 3) {
    // Handling legacy 3-part format: Time | Level | Message
    return {
      raw: data,
      time: parts[0] || '-',
      level: parts[1] || 'INFO',
      source: '-',
      message: parts[2] || ''
    };
  }
  // Fallback for non-standard logs
  return {
    raw: data,
    time: '-',
    level: 'INFO',
    source: '-',
    message: data
  };
};

const connect = () => {
  if (ws) ws.close();
  
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.port === '5173' ? 'localhost:8000' : window.location.host;
  ws = new WebSocket(`${protocol}//${host}/ws/logs`);

  ws.onopen = () => {
    connected.value = true;
    isReconnecting.value = false;
  };

  ws.onmessage = (event) => {
    if (isPaused.value) return;
    
    const entry = parseLog(event.data);
    logs.value.push(entry);
    
    if (logs.value.length > 2000) logs.value.shift();
    
    if (autoScroll.value) {
      scrollToBottom();
    }
  };

  ws.onclose = () => {
    connected.value = false;
    if (!isReconnecting.value) {
      setTimeout(connect, 3000);
    }
  };
};

const scrollToBottom = async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
};

const handleScroll = () => {
  // Manual scroll no longer auto-disables the switch to avoid confusion
  // Unless we want the "smart" behavior, but user wants literal switch control
};

const toggleScroll = (checked: boolean) => {
  if (checked) {
    scrollToBottom();
  }
};

const togglePause = () => {
  isPaused.value = !isPaused.value;
  if (isPaused.value) {
    message.info('日志已播放暂停');
  } else {
    message.success('日志已恢复');
    scrollToBottom();
  }
};

const reconnect = () => {
  isReconnecting.value = true;
  connect();
  message.loading({ content: '正在重新连接...', key: 'reconnect' });
  setTimeout(() => {
    if (connected.value) {
      message.success({ content: '已重新连接', key: 'reconnect', duration: 2 });
    } else {
      message.error({ content: '重新连接失败', key: 'reconnect', duration: 2 });
      isReconnecting.value = false;
    }
  }, 1000);
};

const clearLogs = () => {
  logs.value = [];
  message.success('日志已清空');
};

const downloadLogs = () => {
  const content = logs.value.map(l => l.raw).join('\n');
  const blob = new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `logs_${new Date().toISOString().replace(/[:.]/g, '-')}.log`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  message.success('日志导出成功');
};

const filteredLogs = computed(() => logs.value);

onMounted(() => {
  connect();
});

onUnmounted(() => {
  if (ws) ws.close();
});
</script>

<style scoped>
.log-viewer-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 150px);
  background: #121212;
  border-radius: 8px;
  overflow: hidden;
  color: #e0e0e0;
  font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
  box-shadow: 0 4px 20px rgba(0,0,0,0.5);
}

/* Header */
.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background: #1e1e1e;
  border-bottom: 1px solid #333;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}

.service-select {
  width: 100px;
}

:deep(.ant-select-selector) {
  background: #2a2a2a !important;
  border-color: #444 !important;
  color: #ccc !important;
}

.control-buttons {
  display: flex;
  align-items: center;
  gap: 4px;
  background: #2a2a2a;
  padding: 2px 4px;
  border-radius: 4px;
}

.control-buttons .ant-btn {
  color: #aaa;
  transition: all 0.2s;
}

.control-buttons .ant-btn:hover {
  color: #fff;
  background: #3a3a3a;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.scroll-switch {
  display: flex;
  align-items: center;
  gap: 6px;
}

.switch-label {
  font-size: 12px;
  color: #aaa;
}

/* Body */
.log-body {
  flex: 1;
  padding: 10px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.6;
}

.log-row {
  display: flex;
  gap: 12px;
  margin-bottom: 2px;
  padding: 2px 4px;
  border-radius: 2px;
  transition: background 0.1s;
}

.log-row:hover {
  background: rgba(255,255,255,0.05);
}

.log-col {
  flex-shrink: 0;
}

.time {
  color: #52c41a;
  width: 150px;
  white-space: nowrap;
}

.level {
  width: 50px;
  font-weight: bold;
  text-align: center;
}

.level.info { color: #52c41a; }
.level.debug { color: #888; }
.level.warning { color: #faad14; }
.level.error { color: #ff4d4f; }

.source {
  color: #177ddc;
  width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message {
  flex: 1;
  color: #d4d4d4;
  white-space: pre-wrap;
  word-break: break-all;
}

/* Footer */
.log-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 16px;
  background: #1e1e1e;
  border-top: 1px solid #333;
  font-size: 12px;
  color: #888;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.realtime-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ff4d4f;
  box-shadow: 0 0 5px rgba(255, 77, 79, 0.5);
}

.status-dot.connected {
  background: #52c41a;
  box-shadow: 0 0 5px rgba(82, 196, 26, 0.5);
}

.status-dot.paused {
  background: #faad14;
  box-shadow: 0 0 5px rgba(250, 173, 20, 0.5);
}

.log-count {
  background: #2a2a2a;
  padding: 2px 8px;
  border-radius: 4px;
}

/* Scrollbar styling */
.log-body::-webkit-scrollbar {
  width: 8px;
}

.log-body::-webkit-scrollbar-track {
  background: transparent;
}

.log-body::-webkit-scrollbar-thumb {
  background: #333;
  border-radius: 4px;
}

.log-body::-webkit-scrollbar-thumb:hover {
  background: #444;
}
</style>
