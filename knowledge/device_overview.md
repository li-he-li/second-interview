# 设备基础说明

category: device_overview

## ARMAX-700 机械臂概述

ARMAX-700 是六轴工业机械臂，用于产线抓取、搬运和装配。默认工作模式为 idle，可接收外部指令执行 move、pick、place 等动作。设备状态包括 online（在线）、offline（离线）、error（故障）、maintenance（维护）。

keywords: 机械臂, ARMAX-700, 六轴, 设备, 状态, online, offline, idle

## 工作空间与坐标系

机械臂基坐标系为直角坐标，工作空间为 x、y、z 三轴。安全工作范围为 x ∈ [-1000, 1000]、y ∈ [-1000, 1000]、z ∈ [-1000, 1000]，单位毫米。超出该范围的坐标属于越界，禁止直接执行。

keywords: 坐标系, 工作空间, 坐标, x, y, z, 范围, 越界, 毫米

## 夹爪与执行机构

末端夹爪为气动二指夹爪，最大夹持力 50N。夹爪动作包括 open、close、pick、release。抓取零件前需确认夹爪气压正常且设备处于在线状态。

keywords: 夹爪, 抓取, 夹持力, 50N, pick, 气压, 抓零件

## 设备状态查询

可通过 get_device_status 查询当前设备状态。status=online 且 mode=idle 且 emergency_stop=false 时，机械臂可执行低风险动作。若设备 offline、error 或处于 maintenance，任何动作类请求都应升级为高风险处理。

keywords: 状态查询, 设备状态, online, idle, 能否执行, 可执行, emergency_stop
