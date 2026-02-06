<template>
  <div class="dashboard">
    <a-row :gutter="16">
      <a-col :span="6">
        <a-card title="运行状态" :bordered="false">
          <a-tag color="success">运行中</a-tag>
          <a-tag v-if="version" color="orange">v{{ version }}</a-tag>
        </a-card>
      </a-col>
      <a-col :span="9">
        <a-card title="TG 机器人" :bordered="false">
          <a-tag :color="tgStatus ? 'blue' : 'red'">{{ tgStatus ? '已连接' : '未配置' }}</a-tag>
        </a-card>
      </a-col>
      <a-col :span="9">
        <a-card title="115 网盘" :bordered="false">
          <a-tag :color="p115Status ? 'blue' : 'red'">{{ p115Status ? '已登录' : '未登录' }}</a-tag>
        </a-card>
      </a-col>
    </a-row>
    
    <a-divider />
    
    <a-typography-title :level="4">快速操作</a-typography-title>
    <a-space>
      <a-button @click="handleTestBot" color="blue">测试机器人</a-button>
      <a-button @click="handleTestChannel">测试频道</a-button>
      <a-button @click="checkStatus">刷新状态</a-button>
      <a-button @click="handleCleanupSaveDir" danger>清空保存目录</a-button>
      <a-button @click="handleCleanupRecycleBin" danger>清空回收站</a-button>
    </a-space>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { message, Modal } from 'ant-design-vue';

const tgStatus = ref(false);
const p115Status = ref(false);
const version = ref('');

const checkStatus = async () => {
  try {
    const res = await axios.get('/api/config/');
    tgStatus.value = !!res.data.tg_bot_token;
    p115Status.value = !!res.data.p115_cookie;
    version.value = res.data.version || '';
    message.success('状态已刷新');
  } catch (e) {
    console.error(e);
    message.error('无法连接到后端服务器');
  }
};


const handleTestBot = async () => {
  try {
    const res = await axios.post('/api/config/test-bot');
    if (res.data.status === 'success') {
      message.success('机器人测试消息已发出');
    } else {
      message.error(res.data.message || '测试失败');
    }
  } catch (e) {
    message.error('测试请求失败');
  }
};

const handleTestChannel = async () => {
  try {
    const res = await axios.post('/api/config/test-channel');
    if (res.data.status === 'success') {
      message.success('频道测试消息已发出');
    } else {
      message.error(res.data.message || '测试失败');
    }
  } catch (e) {
    message.error('测试请求失败');
  }
};

const handleCleanupSaveDir = () => {
  Modal.confirm({
    title: '确认清空保存目录？',
    content: '此操作将删除保存目录中的所有文件和文件夹，是否继续？',
    okText: '确认',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const res = await axios.post('/api/config/cleanup-save-dir');
        if (res.data.status === 'success') {
          message.success('保存目录已清空');
        } else {
          message.error(res.data.message || '清空失败');
        }
      } catch (e) {
        message.error('清空请求失败');
      }
    }
  });
};

const handleCleanupRecycleBin = () => {
  Modal.confirm({
    title: '确认清空回收站？',
    content: '此操作将清空115网盘回收站中的所有文件，是否继续？',
    okText: '确认',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const res = await axios.post('/api/config/cleanup-recycle-bin');
        if (res.data.status === 'success') {
          message.success('回收站已清空');
        } else {
          message.error(res.data.message || '清空失败');
        }
      } catch (e) {
        message.error('清空请求失败');
      }
    }
  });
};

onMounted(checkStatus);
</script>
