---
name: HappyFreeTime S-UI1B
description: V4 风格的本地出行规划工作台基础布局
colors:
  action: "#343b38"
  action-hover: "#202622"
  canvas: "#fbfaf7"
  surface: "#ffffff"
  surface-muted: "#f5f5f1"
  inspector: "#fafaf7"
  text: "#303735"
  muted: "#646b66"
  line: "#e6e7e1"
  line-strong: "#c8cdc4"
  accent: "#fff3d5"
  accent-line: "#e7c789"
typography:
  display:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "clamp(24px, 2.2vw, 32px)"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  body:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "0.875rem"
    lineHeight: 1.5
  label:
    fontSize: "0.75rem"
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
---

# Design System: HappyFreeTime S-UI1B

## Overview

**Current implementation，2026-10-19，`codex/s-ui1-product-frontend` 的 B 切片。**
主要依据本地 React Prototype V4；Figma 仅作补充。这里记录已实现的外壳，不能当成整个 V4 迁移完成的说明。

暖白表面、深灰操作、少量淡黄强调，服务于聊天规划与方案检查。Token 定义在 `src/tokens.css`，外壳规则在 `src/shell.css`；`src/styles.css` 保留尚待后续切片迁移的业务展示样式。原有蓝色/玻璃视觉不作为新外壳规范。

## Colors

深灰承担主要操作和文字，白与暖灰区分聊天、检查器及次级容器。淡黄用于当前会话、待确认条件和必要提醒，不作为大面积背景。来源、错误与成功状态仍保留各自语义，不能仅靠颜色传达。

## Typography

使用系统中英文字体栈；字号以共享的 12、13、14、16、20、24px 等级为基础。正文与输入为 14px，次级信息为 12–13px；同类指标共用字号。欢迎主标题使用上述自适应 Display，聊天后的标题为 14px、600 字重。

欢迎态完整保留用户指定的英文品牌行、中文标题和副标题；这不是所有业务模块都需要复制的标题形式。

## Layout

- 高度为 `100dvh`，页面本身不随对话滚动。
- 导航展开 220px、折叠 64px；剩余桌面宽度由聊天与详情各占一半。
- 顶部条件栏为 64px；五个 Chip 顺序为日期时间、出发地、同行人数、人均预算、出行偏好。空间不足时在条件栏内横向滚动。
- 空会话展示完整欢迎标题。已有消息时，44px 标题独立位于聊天滚动区上方；输入区固定在聊天工作区底部。
- 条件草稿与 stale 提示进入工作区正常流，不悬浮覆盖标题或消息。
- 900px 及以下改为单工作区；历史和详情通过现有抽屉访问。聊天、详情、历史列表和条件栏各有滚动边界。

## Elevation & Depth

主要依靠表面与细分隔线建立层次。保留功能卡片的低强度阴影，不为外壳添加玻璃模糊或彩色光晕。历史操作菜单参与列表正常布局，避免被最后一条会话的滚动边界裁切。

## Shapes

8px 用于常规按钮及导航项，12px 用于次级容器，16px 用于输入区。条件 Chip 与发送按钮保留各自的圆形/胶囊轮廓。焦点使用清晰的深灰描边；支持减少动态效果设置。

## Components

- **SessionNavigation**：真实会话、新建、切换、重命名与删除。折叠后 Logo 区提供展开控件；按钮在 hover/focus 显现，触屏常显。切换后焦点移到对应控件，仅折叠偏好存入 localStorage。重命名期间隐藏 More，避免覆盖取消按钮。
- **ConversationHeading**：仅空会话显示欢迎态；已有消息时，顶部栏使用简短品牌标语，不随方案或会话标题变化。
- **PlanningContextBar**：只重排既有条件编辑入口；保存仍提交类型化 Patch。编辑器内容不属于本次完整 V4 控件迁移。
- **PlanningContextNotice**：以横跨聊天和详情区的居中提示条显示后端草稿/待验证状态及既有重规划操作，正常占据布局空间；保存成功的短暂确认通知显示在聊天区左下方。
- **Composer**：固定底部，淡灰表面与深灰发送操作，保留真实忙碌、反问、恢复和修改状态。
- **候选结果与详情栏**：方案卡片上方展示“最推荐”、最多三项偏好标签和本轮推荐解释，不重复顶部硬约束；助手消息以“管家”文字标识，方案回复贴齐正文左侧，并以浅线与上一条用户提问分隔。右侧方案标题随内容滚动，行程/地图/核验标签保持吸顶，“返回已选”位于方案状态行；向下滚动后可用浮动箭头回到顶部。

## Do's and Don'ts

- Do 从共享 Token 扩展外壳，优先修改所属规则，不追加另一套覆盖样式。
- Do 保留全员地点费用、路线累计距离、数据来源及降级语义，展示不能改写业务事实。
- Don't 将 V4 本地状态、示例推荐或演示交易当作真实结果。
- Don't 把本文件推广为已完成的 C–F 规范：高级条件编辑仍按对应切片范围维护。
- Don't 将现有条件弹窗的焦点/ESC 缺口或旧卡片布局固化为未来规范；这些保留功能将在对应切片处理。

## S-UI1D 本地实现说明（2026-10-20）
检查器以“行程 / 地图 / 核验与来源”三个标签切换；证据内容只在独立标签中呈现。POI 详情占用当前检查器区域，不另起导航层，并以当前 response、plan version、plan ID、stop index 和 Stop resource ID 校验上下文。回退动作恢复进入前的标签、滚动与焦点。

地图保留 response.route_legs 的原始索引。POI 标记/地点行打开对应站点，路线段只聚焦路线；缺少 geometry 或地图配置时展示文字列表和状态，不合成坐标。POI 页面区分方案站点费用与展示参考人均，不展示模拟评分/评论作为事实，并将模拟数据与示意图来源明确标注。
