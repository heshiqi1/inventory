# 📤 GitHub 上传指南

## 方法一: 使用批处理脚本 (推荐)

### 步骤:

1. **安装 Git** (如果尚未安装)
   - 下载地址: https://git-scm.com/download/win
   - 安装时选择默认选项即可

2. **双击运行** `git_upload.bat`
   - 脚本会自动完成所有步骤
   - 按提示输入用户名和密码(或 Personal Access Token)

3. **完成!**
   - 访问 https://github.com/heshiqi1/inventory 查看你的项目

---

## 方法二: 手动命令行操作

如果批处理脚本不工作,可以手动执行以下命令:

### 1. 打开命令提示符 (CMD) 或 PowerShell

```bash
cd C:\Users\shiqi.he\Documents\inventory
```

### 2. 初始化 Git 仓库

```bash
git init
```

### 3. 配置用户信息 (首次使用)

```bash
git config user.name "你的GitHub用户名"
git config user.email "你的GitHub邮箱"
```

### 4. 添加所有文件

```bash
git add .
```

### 5. 创建提交

```bash
git commit -m "Initial commit: H1/H2 外汇交易策略监控与回测系统"
```

### 6. 添加远程仓库

```bash
git remote add origin https://github.com/heshiqi1/inventory.git
```

### 7. 重命名主分支

```bash
git branch -M main
```

### 8. 推送到 GitHub

```bash
git push -u origin main
```

---

## 方法三: 使用 GitHub Desktop (最简单)

### 步骤:

1. **下载并安装 GitHub Desktop**
   - 下载地址: https://desktop.github.com/

2. **登录 GitHub 账号**
   - 打开 GitHub Desktop
   - File → Options → Accounts → Sign in

3. **添加本地仓库**
   - File → Add local repository
   - 选择 `C:\Users\shiqi.he\Documents\inventory`
   - 如果提示"不是Git仓库",点击"Create a repository"

4. **创建初始提交**
   - 在左下角输入提交信息: "Initial commit"
   - 点击 "Commit to main"

5. **发布到 GitHub**
   - 点击顶部的 "Publish repository"
   - Repository name: `inventory`
   - 取消勾选 "Keep this code private" (如果你想公开)
   - 点击 "Publish repository"

6. **完成!**

---

## ⚠️ 常见问题

### Q1: 推送时要求输入密码,但密码不正确?

**A**: GitHub 已不支持密码认证,需要使用 Personal Access Token (PAT)

**创建 PAT 步骤**:
1. 访问 https://github.com/settings/tokens
2. 点击 "Generate new token" → "Generate new token (classic)"
3. Note: `inventory-upload`
4. Expiration: 选择过期时间(建议90天或No expiration)
5. 勾选 `repo` (完整仓库访问权限)
6. 点击 "Generate token"
7. **复制生成的 token** (只显示一次!)
8. 推送时使用 token 作为密码

### Q2: git 命令不存在?

**A**: Git 未安装或未添加到 PATH
- 下载安装: https://git-scm.com/download/win
- 安装时勾选 "Add Git to PATH"
- 安装完成后重启命令提示符

### Q3: 文件太大无法上传?

**A**: GitHub 单个文件限制 100MB

已在 `.gitignore` 中排除了图表文件夹:
- `backtest_charts/`
- `backtest_charts_1year/`
- `backtest_charts_5min/`

如需分享图表,可以:
1. 压缩后上传到网盘
2. 使用 Git LFS (大文件存储)
3. 只上传代表性的几张图表

### Q4: 推送失败: "remote contains work that you do not have locally"?

**A**: 远程仓库已有内容

解决方法:
```bash
# 先拉取远程内容
git pull origin main --allow-unrelated-histories

# 然后推送
git push -u origin main
```

### Q5: 如何更新已上传的项目?

**A**: 修改代码后执行:
```bash
git add .
git commit -m "更新说明"
git push
```

---

## 📋 检查清单

上传前确认:

- ✅ `.gitignore` 文件已创建 (排除敏感文件和大文件)
- ✅ `README.md` 文件已创建 (项目说明)
- ✅ 删除或脱敏敏感信息 (企业微信 Webhook URL 等)
- ✅ 图表文件夹已被忽略 (避免上传大文件)

---

## 🎯 上传后的操作建议

### 1. 添加项目描述

访问 https://github.com/heshiqi1/inventory/settings
- 添加 Description: "H1/H2 外汇交易策略监控与回测系统"
- 添加 Topics: `forex`, `trading`, `backtest`, `python`, `technical-analysis`

### 2. 设置仓库可见性

- Public: 任何人都可以看到
- Private: 只有你和授权的人可以看到

### 3. 启用 GitHub Pages (可选)

如果想展示回测报告:
- Settings → Pages
- Source: Deploy from a branch
- Branch: main / docs

### 4. 创建 Release (可选)

标记重要版本:
- Releases → Create a new release
- Tag: v1.0
- Title: "首个稳定版本"
- 上传回测报告PDF或关键图表

---

## 🔐 安全提示

⚠️ **在上传前,务必检查以下内容**:

1. **企业微信 Webhook URL** - 已在代码中设置为示例
2. **API密钥** - 确保没有硬编码
3. **.env 文件** - 已在 .gitignore 中
4. **个人信息** - 检查是否有敏感路径或用户名

如发现已上传敏感信息:
```bash
# 从历史记录中删除文件
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch 敏感文件路径" \
  --prune-empty --tag-name-filter cat -- --all

# 强制推送
git push origin --force --all
```

---

## 📞 需要帮助?

- GitHub 官方文档: https://docs.github.com/cn
- Git 教程: https://www.liaoxuefeng.com/wiki/896043488029600
- 提交 Issue: https://github.com/heshiqi1/inventory/issues

---

**最后更新**: 2026-03-02
