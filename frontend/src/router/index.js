import { createRouter, createWebHistory } from 'vue-router'
import UploadView from '../views/UploadView.vue'
import JobListView from '../views/JobListView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import SpecView from '../views/SpecView.vue'
import P5BannerEditor from '../views/P5BannerEditor.vue'

const routes = [
  { path: '/',              name: 'upload',    component: UploadView },
  { path: '/jobs',          name: 'jobs',      component: JobListView },
  { path: '/job/:id',       name: 'jobDetail', component: JobDetailView },
  { path: '/job/:id/edit',  name: 'jobEdit',   component: P5BannerEditor },
  { path: '/spec',          name: 'spec',      component: SpecView },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
