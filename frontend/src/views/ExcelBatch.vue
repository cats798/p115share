<template>
  <div class="excel-batch-container">
    <template v-if="!currentTaskId && tasks.length === 0 && !uploading">
      <!-- Initial Upload View -->
      <div class="empty-upload-view">
        <div class="header">
          <div class="header-left">
            <a-avatar :size="48" shape="square" style="background-color: #27ae60">
              <template #icon><FileExcelOutlined /></template>
            </a-avatar>
            <div class="header-text">
              <h1>Excel批量转存分享</h1>
              <p>上传包含分享链接的Excel文件，批量转存并同步分享链接</p>
            </div>
          </div>
          <div class="header-right">
            <a-space>
              <a-button @click="downloadTemplate">
                <template #icon><DownloadOutlined /></template>
                下载模板
              </a-button>
              <a-upload
                name="file"
                :show-upload-list="false"
                :before-upload="handleBeforeUpload"
                accept=".xlsx,.xls,.csv"
              >
                <a-button type="primary">
                  <template #icon><UploadOutlined /></template>
                  上传Excel
                </a-button>
              </a-upload>
            </a-space>
          </div>
        </div>

        <div class="main-empty-content">
          <div class="drop-zone">
             <a-upload-dragger
                name="file"
                :show-upload-list="false"
                :before-upload="handleBeforeUpload"
                accept=".xlsx,.xls,.csv"
              >
                <p class="ant-upload-drag-icon">
                  <FileExcelOutlined style="color: #27ae60; font-size: 64px" />
                </p>
                <h2 class="ant-upload-text">请选择或上传一个任务</h2>
                <p class="ant-upload-hint">上传包含分享链接的Excel文件，批量转存并同步分享链接</p>
                <a-button type="primary" size="large" style="margin-top: 24px">
                  <template #icon><UploadOutlined /></template>
                  上传Excel文件
                </a-button>
              </a-upload-dragger>
          </div>
        </div>
      </div>
    </template>

    <template v-else>
      <!-- Main Work View -->
      <div class="work-view">
        <!-- Sidebar Task List -->
        <div class="task-sidebar" :class="{ 'mobile-hidden': isMobile && currentTaskId }">
          <div class="sidebar-header">
            <span>任务列表</span>
            <a-badge :count="tasks.length" :overflow-count="99" />
          </div>
          <div class="sidebar-content">
            <template v-if="tasks.length === 0">
               <div class="empty-tasks">
                 <a-empty description="暂无任务" />
               </div>
            </template>
            <template v-else>
              <div 
                v-for="task in tasks" 
                :key="task.id" 
                class="task-item" 
                :class="{ active: currentTaskId === task.id }"
                @click="selectTask(task.id)"
              >
                <div class="task-icon">
                   <FileExcelOutlined />
                </div>
                <div class="task-info">
                  <div class="task-name">{{ task.name }}</div>
                  <div class="task-meta">
                    <span v-if="task.status === 'running'" class="status-running">运行中</span>
                    <span v-else-if="task.status === 'paused'" class="status-paused">已暂停</span>
                    <span v-else-if="task.status === 'completed'" class="status-completed">已完成</span>
                    <span v-else-if="task.status === 'cancelled'" class="status-cancelled">已取消</span>
                    <span v-else>待处理</span>
                    <span class="count">{{ task.total_count }} 条</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
          <div class="sidebar-footer">
            <a-upload
              name="file"
              :show-upload-list="false"
              :before-upload="handleBeforeUpload"
              accept=".xlsx,.xls,.csv"
            >
              <a-button type="primary" block>
                <template #icon><UploadOutlined /></template>
                上传Excel
              </a-button>
            </a-upload>
          </div>
        </div>

        <!-- Task Content -->
        <div class="task-main" v-if="currentTaskId">
          <div class="task-header">
             <div class="header-left">
               <a-button v-if="isMobile" type="link" @click="currentTaskId = null">
                 <template #icon><LeftOutlined /></template>
               </a-button>
               <span class="task-title">{{ currentTask?.name }}</span>
             </div>
             <div class="header-right">
                <a-button ghost danger @click="handleDeleteTask" size="small">删除任务</a-button>
             </div>
          </div>

          <!-- Stats Cards -->
          <div class="stats-cards">
            <a-row :gutter="16">
              <a-col :span="6">
                <div class="stat-card total">
                  <div class="stat-icon"><FileOutlined /></div>
                  <div class="stat-value">
                     <h3>{{ currentTask?.total_count || 0 }}</h3>
                     <p>总链接数</p>
                  </div>
                </div>
              </a-col>
              <a-col :span="6">
                <div class="stat-card processing">
                  <div class="stat-icon"><SyncOutlined spin v-if="currentTask?.status === 'running'" /> <SyncOutlined v-else /></div>
                  <div class="stat-value">
                     <h3>{{ (currentTask?.success_count || 0) + (currentTask?.fail_count || 0) }}</h3>
                     <p>已处理</p>
                  </div>
                </div>
              </a-col>
              <a-col :span="6">
                <div class="stat-card success">
                  <div class="stat-icon"><CheckCircleOutlined /></div>
                  <div class="stat-value">
                     <h3>{{ currentTask?.success_count || 0 }}</h3>
                     <p>成功</p>
                  </div>
                </div>
              </a-col>
              <a-col :span="6">
                <div class="stat-card error">
                  <div class="stat-icon"><CloseCircleOutlined /></div>
                  <div class="stat-value">
                     <h3>{{ currentTask?.fail_count || 0 }}</h3>
                     <p>失败</p>
                  </div>
                </div>
              </a-col>
            </a-row>
          </div>

          <!-- Control Section -->
          <div class="control-panel">
            <div class="section-title"><SettingOutlined /> 转存分享配置</div>
            <div class="control-form">
               <div class="form-row">
                  <div class="form-item disabled">
                     <label>目标账号</label>
                     <div class="fake-input">115网盘1 <a-tag color="blue">115网盘</a-tag></div>
                  </div>
                  <div class="form-item disabled">
                     <label>目标目录</label>
                     <div class="fake-input">/云下载 <a-button size="small" disabled>选择目录</a-button></div>
                  </div>
               </div>
               <div class="form-row">
                 <div class="form-item">
                    <label>转存间隔</label>
                    <div class="interval-input">
                       <a-input-number :min="1" :default-value="5" /> - <a-input-number :min="1" :default-value="10" /> 秒
                    </div>
                 </div>
               </div>
               <div class="form-actions">
                  <a-space>
                    <a-button 
                      v-if="currentTask?.status === 'wait' || currentTask?.status === 'paused' || currentTask?.status === 'cancelled'" 
                      type="primary" 
                      @click="handleStartTask" 
                      :disabled="selectedRowKeys.length === 0"
                    >
                      开始转存分享
                    </a-button>
                    <a-button v-if="currentTask?.status === 'running'" type="primary" @click="handlePauseTask">
                      暂停转存分享
                    </a-button>
                    <!-- User mentioned "暂停/继续、取消" -->
                    <a-button v-if="currentTask?.status === 'running' || currentTask?.status === 'paused'" @click="handleCancelTask">
                      取消转存分享
                    </a-button>
                  </a-space>
               </div>
            </div>
          </div>

          <!-- Links List -->
          <div class="links-list-container">
            <div class="list-header">
               <div class="section-title"><UnorderedListOutlined /> 分享链接列表</div>
               <div class="header-actions">
                  <a-space>
                     <a-button size="small" @click="selectAll">全选</a-button>
                     <a-button size="small" @click="selectedRowKeys = []">取消全选</a-button>
                  </a-space>
               </div>
            </div>
            
            <!-- Filter & Search -->
            <div class="filter-bar">
               <a-select v-model:value="filterStatus" placeholder="选择状态" style="width: 120px" allow-clear @change="fetchItems">
                  <a-select-option value="待处理">待处理</a-select-option>
                  <a-select-option value="处理中">处理中</a-select-option>
                  <a-select-option value="成功">成功</a-select-option>
                  <a-select-option value="失败">失败</a-select-option>
                  <a-select-option value="跳过">跳过</a-select-option>
               </a-select>
               <a-input-search v-model:value="searchQuery" placeholder="搜索资源名称" style="width: 200px" @search="fetchItems" />
            </div>

            <a-table
              :columns="columns"
              :data-source="items"
              :pagination="pagination"
              :loading="itemsLoading"
              :row-selection="rowSelection"
              row-key="id"
              size="small"
              @change="handleTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'original_url'">
                  <a :href="record.original_url" target="_blank">{{ record.original_url }}</a>
                </template>
                <template v-if="column.key === 'status'">
                  <a-tag :color="getStatusColor(record.status)">{{ record.status }}</a-tag>
                </template>
                <template v-if="column.key === 'error_msg'">
                  <span style="color: #ff4d4f">{{ record.error_msg }}</span>
                </template>
              </template>
            </a-table>
          </div>
        </div>
        
        <div class="task-main empty" v-else>
           <a-empty description="请选择一个任务或上传新文件" />
        </div>
      </div>
    </template>

    <!-- Column Mapping Modal -->
    <a-modal
      v-model:open="mappingModalVisible"
      title="选择Excel列映射"
      @ok="handleMappingOk"
      :confirmLoading="creatingTask"
      width="800px"
    >
      <div class="mapping-content">
        <div class="file-summary">
           <div class="summary-item">
              <span class="label">文件名</span>
              <span class="value">{{ pendingFile?.name }}</span>
           </div>
           <div class="summary-item">
              <span class="label">数据行数</span>
              <span class="value">{{ parseResult?.total_rows }} 行</span>
           </div>
        </div>

        <div class="mapping-form">
           <div class="mapping-row">
              <div class="mapping-label"><LinkOutlined /> 链接列 <span>*</span></div>
              <a-select v-model:value="mapping.link" placeholder="请选择链接所在列" style="width: 100%">
                 <a-select-option v-for="h in parseResult?.headers" :key="h" :value="h">{{ h }}</a-select-option>
              </a-select>
              <div class="header-suggest" v-if="mapping.link">
                 样本：{{ parseResult?.preview[0][mapping.link] }}
              </div>
           </div>

           <div class="mapping-row">
              <div class="mapping-label"><FileTextOutlined /> 描述列 (可选)</div>
              <a-select v-model:value="mapping.title" placeholder="选择资源名称/标题列" style="width: 100%" allow-clear>
                 <a-select-option v-for="h in parseResult?.headers" :key="h" :value="h">{{ h }}</a-select-option>
              </a-select>
           </div>

           <div class="mapping-row">
              <div class="mapping-label"><KeyOutlined /> 访问码列 (可选)</div>
              <a-select v-model:value="mapping.code" placeholder="选择提取码/访问码列" style="width: 100%" allow-clear>
                 <a-select-option v-for="h in parseResult?.headers" :key="h" :value="h">{{ h }}</a-select-option>
              </a-select>
           </div>
        </div>

        <div class="mapping-preview">
           <h3>所有列预览</h3>
           <div class="preview-scroll">
             <table class="preview-table">
               <thead>
                 <tr>
                   <th>列</th>
                   <th>表头</th>
                   <th>样本值</th>
                   <th>建议</th>
                 </tr>
               </thead>
               <tbody>
                 <tr v-for="(h, idx) in parseResult?.headers" :key="h">
                   <td>{{ String.fromCharCode(65 + Number(idx)) }}</td>
                   <td>{{ h }}</td>
                   <td>{{ parseResult?.preview[0][h] }}</td>
                   <td>
                     <a-tag v-if="isLinkCol(h)" color="success">链接</a-tag>
                     <a-tag v-else-if="isTitleCol(h)" color="processing">描述</a-tag>
                     <a-tag v-else-if="isCodeCol(h)" color="warning">访问码</a-tag>
                   </td>
                 </tr>
               </tbody>
             </table>
           </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed, onUnmounted } from 'vue';
import { 
  FileExcelOutlined, 
  DownloadOutlined, 
  UploadOutlined,
  FileOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SettingOutlined,
  UnorderedListOutlined,
  LinkOutlined,
  FileTextOutlined,
  KeyOutlined,
  LeftOutlined
} from '@ant-design/icons-vue';
import { message, Modal, Grid, theme } from 'ant-design-vue';
import axios from 'axios';

const { useToken } = theme;
const { token: antdToken } = useToken();

const useBreakpoint = Grid.useBreakpoint;
const screens = useBreakpoint();
const isMobile = computed(() => !screens.value.md && (screens.value.sm || screens.value.xs));

// State
const tasks = ref<any[]>([]);
const currentTaskId = ref<number | null>(null);
const currentTask = computed(() => tasks.value.find(t => t.id === currentTaskId.value));
const items = ref<any[]>([]);
const itemsLoading = ref(false);
const pagination = reactive({
  current: 1,
  pageSize: 50,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20', '50', '100'],
});

const filterStatus = ref<string | undefined>(undefined);
const searchQuery = ref('');
const selectedRowKeys = ref<any[]>([]);

// Upload & Mapping
const mappingModalVisible = ref(false);
const uploading = ref(false);
const creatingTask = ref(false);
const pendingFile = ref<File | null>(null);
const parseResult = ref<any>(null);
const mapping = reactive({
  link: undefined,
  title: undefined,
  code: undefined
});

// Timer for polling
let pollTimer: any = null;

const columns = [
  { title: '行号', dataIndex: 'row_index', key: 'row_index', width: 70 },
  { title: '分享链接', dataIndex: 'original_url', key: 'original_url', ellipsis: true },
  { title: '资源名称', dataIndex: 'title', key: 'title', ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
  { title: '错误信息', dataIndex: 'error_msg', key: 'error_msg', ellipsis: true },
];

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: any[]) => {
    selectedRowKeys.value = keys;
  },
}));

// Methods
const fetchTasks = async () => {
  try {
    const res = await axios.get('/api/excel/tasks');
    if (res.data.status === 'success') {
      tasks.value = res.data.data;
    }
  } catch (e) {
    console.error(e);
  }
};

const fetchItems = async () => {
  if (!currentTaskId.value) return;
  itemsLoading.value = true;
  try {
    const res = await axios.get(`/api/excel/tasks/${currentTaskId.value}/items`, {
      params: {
        page: pagination.current,
        page_size: pagination.pageSize,
        status: filterStatus.value
      }
    });
    if (res.data.status === 'success') {
      items.value = res.data.data;
      pagination.total = res.data.total;
    }
  } catch (e) {
    console.error(e);
  } finally {
    itemsLoading.value = false;
  }
};

const selectTask = (id: number) => {
  currentTaskId.value = id;
  pagination.current = 1;
  fetchItems();
};

const handleBeforeUpload = async (file: File) => {
  pendingFile.value = file;
  const formData = new FormData();
  formData.append('file', file);
  
  uploading.value = true;
  try {
    const res = await axios.post('/api/excel/parse', formData);
    if (res.data.status === 'success') {
      parseResult.value = res.data.data;
      // Auto suggest mapping
      mapping.link = res.data.data.headers.find((h: string) => isLinkCol(h));
      mapping.title = res.data.data.headers.find((h: string) => isTitleCol(h));
      mapping.code = res.data.data.headers.find((h: string) => isCodeCol(h));
      
      mappingModalVisible.value = true;
    } else {
      message.error(res.data.message || '文件解析失败');
    }
  } catch (e) {
    message.error('上传解析失败');
  } finally {
    uploading.value = false;
  }
  return false; // Prevent auto upload
};

const handleMappingOk = async () => {
  if (!mapping.link) {
    message.warning('请选择链接列');
    return;
  }
  
  creatingTask.value = true;
  const formData = new FormData();
  formData.append('file', pendingFile.value!);
  formData.append('filename', pendingFile.value!.name);
  formData.append('mapping', JSON.stringify(mapping));
  
  try {
    const res = await axios.post('/api/excel/tasks', formData);
    if (res.data.status === 'success') {
      message.success('任务创建成功');
      mappingModalVisible.value = false;
      await fetchTasks();
      selectTask(res.data.task_id);
    } else {
      message.error(res.data.message || '创建任务失败');
    }
  } catch (e) {
    message.error('请求失败');
  } finally {
    creatingTask.value = false;
  }
};

const handleStartTask = async () => {
  if (!currentTaskId.value) return;
  try {
    await axios.post(`/api/excel/tasks/${currentTaskId.value}/start`, {
      item_ids: selectedRowKeys.value
    });
    message.success('正在启动转存分享...');
    await fetchTasks();
  } catch (e) {
    message.error('启动失败');
  }
};

const handlePauseTask = async () => {
  if (!currentTaskId.value) return;
  try {
    await axios.post(`/api/excel/tasks/${currentTaskId.value}/pause`);
    message.success('已暂停');
    await fetchTasks();
  } catch (e) {
    message.error('请求失败');
  }
};

const handleCancelTask = async () => {
  if (!currentTaskId.value) return;
  Modal.confirm({
     title: '确认取消转存？',
     content: '取消后将停止当前任务的处理，是否继续？',
     onOk: async () => {
        try {
          await axios.post(`/api/excel/tasks/${currentTaskId.value}/pause`); 
          message.success('任务已取消处理');
          await fetchTasks();
        } catch (e) {
          message.error('请求失败');
        }
     }
  });
};

const handleDeleteTask = () => {
  if (!currentTaskId.value) return;
  Modal.confirm({
    title: '确认删除任务？',
    content: '此操作将彻底删除任务及其所有链接记录，是否继续？',
    okType: 'danger',
    onOk: async () => {
      try {
        await axios.delete(`/api/excel/tasks/${currentTaskId.value}`);
        message.success('任务已删除');
        currentTaskId.value = null;
        await fetchTasks();
      } catch (e) {
        message.error('删除失败');
      }
    }
  });
};

const handleTableChange = (pag: any) => {
  pagination.current = pag.current;
  fetchItems();
};

const selectAll = () => {
  selectedRowKeys.value = items.value.map(i => i.id);
};

const downloadTemplate = () => {
  window.open('/static/template/分享链接导入模板.xlsx', '_blank');
};

const getStatusColor = (status: string) => {
  switch (status) {
    case '成功': return 'success';
    case '失败': return 'error';
    case '处理中': return 'processing';
    case '跳过': return 'default';
    case '待处理': return 'warning';
    default: return 'default';
  }
};

// Helpers for suggest
const isLinkCol = (h: string) => /链接|url|link|s\/|115/i.test(h);
const isTitleCol = (h: string) => /标题|名称|资源|name|title/i.test(h);
const isCodeCol = (h: string) => /提取码|访问码|密码|code|pwd|password|msg/i.test(h);

onMounted(() => {
  fetchTasks();
  pollTimer = setInterval(() => {
    fetchTasks();
    if (currentTask.value?.status === 'running') {
      fetchItems();
    }
  }, 3000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.excel-batch-container {
  height: 100%;
  color: v-bind('antdToken.colorText');
}

.empty-upload-view {
  max-width: 1200px;
  margin: 0 auto;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 48px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-text h1 {
  margin: 0;
  font-size: 24px;
}

.header-text p {
  margin: 4px 0 0;
  color: v-bind('antdToken.colorTextDescription');
}

.main-empty-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 0;
}

.drop-zone {
  width: 100%;
  max-width: 600px;
}

.work-view {
  display: flex;
  height: calc(100vh - 120px);
  gap: 16px;
}

.task-sidebar {
  width: 260px;
  border-right: 1px solid v-bind('antdToken.colorBorderSecondary');
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 16px;
  font-weight: bold;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid v-bind('antdToken.colorBorderSecondary');
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.task-item {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: all 0.3s;
}

.task-item:hover {
  background: v-bind('antdToken.colorFillAlter');
}

.task-item.active {
  background: v-bind('antdToken.controlItemBgActive');
  border-right: 3px solid v-bind('antdToken.colorPrimary');
}

.task-icon {
  font-size: 20px;
  color: #27ae60;
}

.task-name {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.task-meta {
  font-size: 12px;
  display: flex;
  gap: 8px;
  color: v-bind('antdToken.colorTextDescription');
}

.status-running { color: v-bind('antdToken.colorPrimary'); }
.status-paused { color: v-bind('antdToken.colorWarning'); }
.status-completed { color: v-bind('antdToken.colorSuccess'); }
.status-cancelled { color: v-bind('antdToken.colorError'); }

.sidebar-footer {
  padding: 16px;
  border-top: 1px solid v-bind('antdToken.colorBorderSecondary');
}

.task-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding-right: 8px;
}

.task-main.empty {
  align-items: center;
  justify-content: center;
}

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.task-title {
  font-size: 18px;
  font-weight: bold;
}

.stats-cards {
  margin-bottom: 24px;
}

.stat-card {
  padding: 16px;
  background: v-bind('antdToken.colorFillAlter');
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 16px;
  height: 80px;
}

.stat-icon {
  font-size: 24px;
  padding: 8px;
  border-radius: 4px;
  background: v-bind('antdToken.colorBgContainer');
}

.total .stat-icon { color: v-bind('antdToken.colorPrimary'); }
.processing .stat-icon { color: v-bind('antdToken.colorTextPlaceholder'); }
.success .stat-icon { color: v-bind('antdToken.colorSuccess'); }
.error .stat-icon { color: v-bind('antdToken.colorError'); }

.stat-value h3 {
  margin: 0;
  font-size: 20px;
}

.stat-value p {
  margin: 0;
  font-size: 12px;
  color: v-bind('antdToken.colorTextDescription');
}

.control-panel {
  padding: 16px;
  background: v-bind('antdToken.colorFillAlter');
  border-radius: 8px;
  margin-bottom: 24px;
}

.section-title {
  font-weight: bold;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.control-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-row {
  display: flex;
  gap: 24px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.form-item label {
  font-size: 12px;
  color: v-bind('antdToken.colorTextDescription');
}

.fake-input {
  height: 32px;
  background: v-bind('antdToken.colorFillTertiary');
  border-radius: 4px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  justify-content: space-between;
  font-size: 14px;
}

.form-item.disabled { color: v-bind('antdToken.colorTextDisabled'); }

.form-actions {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.form-actions .hint {
  font-size: 12px;
  color: v-bind('antdToken.colorError');
}

.links-list-container {
  flex: 1;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.mapping-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.file-summary {
  padding: 16px;
  background: v-bind('antdToken.colorFillTertiary');
  border-radius: 8px;
  display: flex;
  gap: 48px;
}

.summary-item {
  display: flex;
  flex-direction: column;
}

.summary-item .label {
  font-size: 12px;
  color: v-bind('antdToken.colorTextDescription');
}

.summary-item .value {
  font-weight: bold;
}

.mapping-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mapping-label {
  margin-bottom: 8px;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mapping-label span { color: v-bind('antdToken.colorError'); }

.header-suggest {
  font-size: 11px;
  color: v-bind('antdToken.colorTextDescription');
  margin-top: 4px;
}

.preview-scroll {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid v-bind('antdToken.colorBorderSecondary');
  border-radius: 4px;
}

.preview-table {
  width: 100%;
  border-collapse: collapse;
}

.preview-table th, .preview-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid v-bind('antdToken.colorBorderSecondary');
  font-size: 13px;
}

.preview-table th {
  background: v-bind('antdToken.colorFillAlter');
  position: sticky;
  top: 0;
}

@media (max-width: 768px) {
  .work-view { flex-direction: column; }
  .task-sidebar { width: 100%; height: 200px; border-right: none; border-bottom: 1px solid v-bind('antdToken.colorBorderSecondary'); }
  .mobile-hidden { display: none; }
  .form-row { flex-direction: column; gap: 12px; }
  .stat-card { flex-direction: column; height: auto; padding: 12px; text-align: center; }
  .stats-cards .ant-col { margin-bottom: 12px; }
}
</style>
