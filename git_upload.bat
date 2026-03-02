@echo off
chcp 65001
echo ========================================
echo GitHub 项目上传脚本
echo ========================================
echo.

REM 检查是否安装了Git
where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未检测到 Git，请先安装 Git
    echo.
    echo 下载地址: https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

echo [1/7] 初始化 Git 仓库...
git init
if %ERRORLEVEL% NEQ 0 (
    echo [错误] Git 初始化失败
    pause
    exit /b 1
)

echo.
echo [2/7] 配置 Git 用户信息 (如果尚未配置)...
git config user.name >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    set /p USERNAME="请输入你的 GitHub 用户名: "
    git config user.name "!USERNAME!"
)

git config user.email >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    set /p EMAIL="请输入你的 GitHub 邮箱: "
    git config user.email "!EMAIL!"
)

echo.
echo [3/7] 添加所有文件到暂存区...
git add .
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 添加文件失败
    pause
    exit /b 1
)

echo.
echo [4/7] 创建初始提交...
git commit -m "Initial commit: H1/H2 外汇交易策略监控与回测系统

功能特性:
- 实时监控系统 (企业微信推送)
- 完整回测框架 (日线/5分钟)
- 双EMA+RSI+H1/H2策略
- 分批止盈风险管理
- 详细交易图表生成

回测结果:
- XAUUSD: 胜率66.7%, 收益率+16.8%
- 包含最近1年完整回测报告
- 100+交易图表示例"

if %ERRORLEVEL% NEQ 0 (
    echo [错误] 提交失败
    pause
    exit /b 1
)

echo.
echo [5/7] 添加远程仓库...
git remote add origin https://github.com/heshiqi1/inventory.git
if %ERRORLEVEL% NEQ 0 (
    echo [警告] 远程仓库可能已存在，尝试更新...
    git remote set-url origin https://github.com/heshiqi1/inventory.git
)

echo.
echo [6/7] 重命名主分支为 main...
git branch -M main
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 重命名分支失败
    pause
    exit /b 1
)

echo.
echo [7/7] 推送到 GitHub...
echo.
echo 注意: 首次推送需要输入 GitHub 用户名和密码(或 Personal Access Token)
echo.
git push -u origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 推送失败
    echo.
    echo 可能的原因:
    echo 1. 需要身份验证 (使用 Personal Access Token)
    echo 2. 网络连接问题
    echo 3. 仓库权限问题
    echo.
    echo Personal Access Token 创建方式:
    echo 1. 访问 https://github.com/settings/tokens
    echo 2. 点击 "Generate new token" (classic)
    echo 3. 选择 "repo" 权限
    echo 4. 复制生成的 token
    echo 5. 推送时使用 token 作为密码
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✅ 上传成功！
echo ========================================
echo.
echo 项目地址: https://github.com/heshiqi1/inventory
echo.
echo 你现在可以:
echo 1. 访问上述链接查看你的项目
echo 2. 编辑 README.md 添加更多说明
echo 3. 继续开发并使用以下命令推送更新:
echo    git add .
echo    git commit -m "更新说明"
echo    git push
echo.
pause
