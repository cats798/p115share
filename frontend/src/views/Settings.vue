<template>
  <div class="settings">
    <a-form :model="formState" layout="vertical" @finish="onFinish">
      <a-typography-title :level="5">Telegram 配置</a-typography-title>
      <a-form-item label="Bot Token" name="tg_bot_token">
        <a-input-password v-model:value="formState.tg_bot_token" placeholder="请输入 TG Bot Token" />
      </a-form-item>
      <a-form-item label="Channel ID" name="tg_channel_id">
        <a-input v-model:value="formState.tg_channel_id" placeholder="例如 @channel_name 或 -100xxxx" />
      </a-form-item>
      <a-form-item label="User ID (通知接收)" name="tg_user_id">
        <a-input v-model:value="formState.tg_user_id" placeholder="接收保存成功通知的用户 ID" />
      </a-form-item>
      <a-form-item label="Chat ID 白名单" name="tg_allow_chats">
        <a-input v-model:value="formState.tg_allow_chats" placeholder="允许使用机器人的 ID (多个用逗号分隔)，留空则不限制" />
      </a-form-item>

      <a-divider />

      <a-typography-title :level="5">115 网盘配置</a-typography-title>
      <a-form-item label="Cookie" name="p115_cookie">
        <a-textarea v-model:value="formState.p115_cookie" :rows="4" placeholder="请输入 115 Cookie (UID=...; CID=...)" />
      </a-form-item>
      <a-form-item label="保存路径" name="p115_save_dir">
        <a-input v-model:value="formState.p115_save_dir" placeholder="例如 /分享保存" />
      </a-form-item>
      <a-form-item label="清理保存目录" name="p115_cleanup_dir_cron">
        <a-input v-model:value="formState.p115_cleanup_dir_cron" placeholder="Cron表达式，例如 */30 * * * * (每30分钟)" />
        <div class="text-gray-500 text-sm mt-1">定时清理保存目录中的文件</div>
      </a-form-item>
      <a-form-item label="清空回收站" name="p115_cleanup_trash_cron">
        <a-input v-model:value="formState.p115_cleanup_trash_cron" placeholder="Cron表达式，例如 0 */2 * * * (每2小时)" />
        <div class="text-gray-500 text-sm mt-1">定时清空115网盘回收站</div>
      </a-form-item>
      <a-form-item label="回收站密码" name="p115_recycle_password">
        <a-input-password v-model:value="formState.p115_recycle_password" placeholder="留空则无密码" />
        <div class="text-gray-500 text-sm mt-1">如果115回收站设置了密码，请在此输入</div>
      </a-form-item>

      <a-form-item>
        <a-button type="primary" html-type="submit" :loading="loading">保存配置</a-button>
      </a-form-item>
    </a-form>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue';
import axios from 'axios';
import { message } from 'ant-design-vue';

const loading = ref(false);
const formState = reactive({
  tg_bot_token: '',
  tg_channel_id: '',
  tg_user_id: '',
  tg_allow_chats: '',
  p115_cookie: '',
  p115_save_dir: '',
  p115_cleanup_dir_cron: '',
  p115_cleanup_trash_cron: '',
  p115_recycle_password: ''
});

const loadConfig = async () => {
  try {
    const res = await axios.get('/api/config/');
    formState.tg_bot_token = res.data.tg_bot_token || '';
    formState.tg_channel_id = res.data.tg_channel_id || '';
    formState.tg_user_id = res.data.tg_user_id || '';
    formState.tg_allow_chats = res.data.tg_allow_chats || '';
    formState.p115_cookie = res.data.p115_cookie || '';
    formState.p115_save_dir = res.data.p115_save_dir || '';
    formState.p115_cleanup_dir_cron = res.data.p115_cleanup_dir_cron || '';
    formState.p115_cleanup_trash_cron = res.data.p115_cleanup_trash_cron || '';
    formState.p115_recycle_password = res.data.p115_recycle_password || '';
  } catch (e) {
    console.error(e);
  }
};

const onFinish = async (values: any) => {
  loading.value = true;
  try {
    await axios.post('/api/config/update', values);
    message.success('配置已保存');
  } catch (e) {
    message.error('保存失败');
  } finally {
    loading.value = false;
  }
};

onMounted(loadConfig);
</script>
