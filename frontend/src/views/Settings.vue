<template>
  <div class="settings">
    <a-form 
      ref="formRef"
      :model="formState" 
      :rules="rules"
      layout="vertical" 
      @finish="onFinish" 
      size="middle"
    >
      <a-typography-title :level="5" style="margin-bottom: 24px">系统配置</a-typography-title>
      
      <a-collapse ghost default-active-key="tg">
        <a-collapse-panel key="tg" header="Telegram 配置">
          <a-form-item label="Bot Token" name="tg_bot_token">
            <a-input-password v-model:value="formState.tg_bot_token" placeholder="请输入 TG Bot Token" />
          </a-form-item>
          <a-form-item label="Channel ID" name="tg_channel_id" extra="注意：Bot 必须被设为该频道的管理员才能发送消息">
            <a-input v-model:value="formState.tg_channel_id" placeholder="例如 @channel_name 或 -100xxxx" />
          </a-form-item>
          <a-form-item label="User ID" name="tg_user_id">
            <a-input v-model:value="formState.tg_user_id" placeholder="接收保存成功通知的用户 ID" />
          </a-form-item>
          <a-form-item label="Chat ID 白名单" name="tg_allow_chats">
            <a-input v-model:value="formState.tg_allow_chats" placeholder="允许使用机器人的 ID (多个用逗号分隔)" />
          </a-form-item>
        </a-collapse-panel>

        <a-collapse-panel key="p115" header="115 网盘配置">
          <a-form-item label="Cookie" name="p115_cookie">
            <a-textarea v-model:value="formState.p115_cookie" :rows="4" placeholder="请输入 115 Cookie" />
          </a-form-item>
          <a-form-item label="保存路径" name="p115_save_dir">
            <a-input v-model:value="formState.p115_save_dir" placeholder="例如 /分享保存" />
          </a-form-item>
          <a-form-item label="清理保存目录 (Cron)" name="p115_cleanup_dir_cron">
            <a-input v-model:value="formState.p115_cleanup_dir_cron" placeholder="例如 */30 * * * *" />
            <div style="font-size: 12px; color: #999; margin-top: 4px">为空则不进行定时清理</div>
          </a-form-item>
          <a-form-item label="清空回收站 (Cron)" name="p115_cleanup_trash_cron">
            <a-input v-model:value="formState.p115_cleanup_trash_cron" placeholder="例如 0 */2 * * *" />
            <div style="font-size: 12px; color: #999; margin-top: 4px">为空则不进行定时清空</div>
          </a-form-item>
          <a-form-item label="回收站密码" name="p115_recycle_password">
            <a-input-password v-model:value="formState.p115_recycle_password" placeholder="留空则无密码" />
          </a-form-item>
        </a-collapse-panel>
      </a-collapse>

      <a-form-item style="margin-top: 24px">
        <a-button type="primary" html-type="submit" :loading="loading" block>保存配置</a-button>
      </a-form-item>
    </a-form>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue';
import axios from 'axios';
import { message } from 'ant-design-vue';

const loading = ref(false);
const formRef = ref();
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

const validateCron = (_rule: any, value: string) => {
  if (!value) return Promise.resolve();
  // Simple cron regex for 5 fields
  const cronRegex = /^(\*|[0-5]?\d)(?:\/[0-5]?\d)?\s+(\*|[01]?\d|2[0-3])(?:\/[01]?\d|2[0-3])?\s+(\*|0?[1-9]|[12]\d|3[01])(?:\/0?[1-9]|[12]\d|3[01])?\s+(\*|0?[1-9]|1[0-2])(?:\/0?[1-9]|1[0-2])?\s+(\*|[0-6])(?:\/[0-6])?$/;
  if (cronRegex.test(value)) {
    return Promise.resolve();
  }
  return Promise.reject('请输入有效的 Cron 表达式 (例如 */30 * * * *)');
};

const rules = {
  tg_bot_token: [{ required: true, message: '请输入 Bot Token', trigger: 'blur' }],
  tg_channel_id: [{ required: true, message: '请输入 Channel ID', trigger: 'blur' }],
  tg_user_id: [{ required: true, message: '请输入 User ID', trigger: 'blur' }],
  tg_allow_chats: [{ required: true, message: '请输入 Chat ID 白名单', trigger: 'blur' }],
  p115_cookie: [{ required: true, message: '请输入 Cookie', trigger: 'blur' }],
  p115_save_dir: [{ required: true, message: '请输入保存路径', trigger: 'blur' }],
  p115_cleanup_dir_cron: [{ validator: validateCron, trigger: 'blur' }],
  p115_cleanup_trash_cron: [{ validator: validateCron, trigger: 'blur' }]
};

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

const onFinish = async () => {
  try {
    await formRef.value.validate();
    loading.value = true;
    await axios.post('/api/config/update', formState);
    message.success('配置已保存');
  } catch (e: any) {
    if (e.errorFields) {
      message.error('请检查表单填写是否正确');
    } else {
      console.error(e);
      message.error(e.response?.data?.detail?.[0]?.msg || '保存失败');
    }
  } finally {
    loading.value = false;
  }
};

onMounted(loadConfig);
</script>
