# Docker Volume 说明

此目录用于 Docker 容器中用户上传的头像持久化存储。

## 使用方法

在 `docker-compose.yml` 中已配置：

```yaml
volumes:
  - ./avatars:/app/backend/static/avatars
```

## 注意事项

- 此目录会自动映射到容器内的 `/app/backend/static/avatars`
- 更新 Docker 镜像后，此目录中的头像文件不会丢失
- 确保此目录有读写权限
