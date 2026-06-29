<template>
  <el-container style="min-height: 100vh;">
    <el-header style="background:#1a1a2e; display:flex; align-items:center; padding:0 24px;">
      <span style="color:#fff; font-size:18px; font-weight:700; margin-right:32px;">
        🎨 Creative Resizer
      </span>
      <el-menu
        :default-active="$route.path"
        mode="horizontal"
        background-color="#1a1a2e"
        text-color="#ccc"
        active-text-color="#fff"
        router
        style="border-bottom:none; flex:1;"
      >
        <el-menu-item index="/">배너 생성</el-menu-item>
        <el-menu-item index="/jobs">작업 목록</el-menu-item>
        <el-menu-item index="/spec">규격 관리</el-menu-item>
      </el-menu>
    </el-header>

    <el-main style="background:#f5f7fa; padding:32px;">
      <router-view />
    </el-main>
  </el-container>
</template>

<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; }
</style>
