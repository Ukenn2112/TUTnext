# app/services/google_classroom.py

# 已获取用户权限有:
# https://www.googleapis.com/auth/classroom.courses.readonly 
# https://www.googleapis.com/auth/classroom.coursework.me.readonly 
# https://www.googleapis.com/auth/classroom.student-submissions.me.readonly


import os
import logging
import aiohttp
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from app.database import db_manager


class GoogleClassroomAPI:
    """Google Classroom API 异步管理类"""
    
    def __init__(self):
        self.client_id = os.getenv("CLIENT_ID")
        if not self.client_id:
            raise ValueError("CLIENT_ID environment variable is required")
        
        self.base_url = "https://classroom.googleapis.com/v1"
        self.oauth_url = "https://oauth2.googleapis.com/token"
        self.token_info_url = "https://oauth2.googleapis.com/tokeninfo"
        
    async def _make_request(
        self, 
        session: aiohttp.ClientSession, 
        method: str, 
        url: str, 
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """发送HTTP请求的通用方法"""
        try:
            async with session.request(method, url, headers=headers, **kwargs) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"Request failed: {response.status} - {await response.text()}")
                    return None
        except Exception as e:
            logging.error(f"Request error: {e}")
            return None
    
    async def _check_token_validity(self, access_token: str) -> bool:
        """检查访问令牌是否有效"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.token_info_url}?access_token={access_token}"
            response = await self._make_request(session, "GET", url)
            
            if response and "error" not in response:
                # 检查令牌是否还有足够的有效时间（至少5分钟）
                expires_in = response.get("expires_in", 0)
                return int(expires_in) > 300  # 5分钟
            return False
    
    async def _refresh_access_token(self, username: str, refresh_token: str) -> Optional[str]:
        """刷新访问令牌"""
        async with aiohttp.ClientSession() as session:
            data = {
                "client_id": self.client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
            
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = await self._make_request(
                session, "POST", self.oauth_url, 
                headers=headers, data=urlencode(data)
            )
            
            if response and "access_token" in response:
                new_access_token = response["access_token"]
                # 更新数据库中的令牌
                success = await db_manager.upsert_user_tokens(
                    username, new_access_token, refresh_token
                )
                if success:
                    logging.info(f"用户 {username} 的访问令牌已刷新")
                    return new_access_token
                else:
                    await db_manager.revoke_user_tokens(username)
                    logging.error(f"更新用户 {username} 令牌失败")
            else:
                await db_manager.revoke_user_tokens(username)
                logging.error(f"刷新用户 {username} 访问令牌失败")
            
            return None
    
    async def _get_valid_access_token(self, username: str) -> Optional[str]:
        """获取有效的访问令牌，如果无效则尝试刷新"""
        # 从数据库获取用户令牌
        tokens = await db_manager.get_user_tokens(username)
        if not tokens:
            logging.warning(f"用户 {username} 没有存储的令牌")
            return None
        
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        
        if not access_token or not refresh_token:
            await db_manager.revoke_user_tokens(username)
            logging.warning(f"用户 {username} 令牌不完整")
            return None
        
        # 检查访问令牌是否有效
        if await self._check_token_validity(access_token):
            return access_token
        
        # 令牌无效，尝试刷新
        logging.info(f"用户 {username} 的访问令牌已过期，正在刷新...")
        return await self._refresh_access_token(username, refresh_token)
    
    async def _get_active_courses(self, session: aiohttp.ClientSession, access_token: str) -> List[Dict[str, Any]]:
        """获取用户所有活跃课程"""
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.base_url}/courses?courseStates=ACTIVE"
        
        response = await self._make_request(session, "GET", url, headers=headers)
        if response and "courses" in response:
            return response["courses"]
        return []
    
    async def _get_course_work_batch(
        self, 
        session: aiohttp.ClientSession, 
        access_token: str, 
        course_ids: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批处理获取多个课程的课题"""
        headers = {"Authorization": f"Bearer {access_token}"}
        course_work_map = {}
        
        # 使用异步并发请求
        async def fetch_course_work(course_id: str):
            url = f"{self.base_url}/courses/{course_id}/courseWork"
            response = await self._make_request(session, "GET", url, headers=headers)
            if response and "courseWork" in response:
                course_work_map[course_id] = response["courseWork"]
            else:
                course_work_map[course_id] = []
        
        # 并发执行所有请求
        tasks = [fetch_course_work(course_id) for course_id in course_ids]
        await asyncio.gather(*tasks)
        
        return course_work_map
    
    async def _get_student_submissions_batch(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        course_work_items: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批处理获取课题的学生提交状态"""
        headers = {"Authorization": f"Bearer {access_token}"}
        submissions_map = {}
        
        async def fetch_submissions(course_work: Dict[str, Any]):
            course_id = course_work["courseId"]
            course_work_id = course_work["id"]
            key = f"{course_id}_{course_work_id}"
            
            url = f"{self.base_url}/courses/{course_id}/courseWork/{course_work_id}/studentSubmissions"
            params = {"states": ["NEW", "CREATED", "RECLAIMED_BY_STUDENT"]}
            
            # 构建查询参数
            query_params = []
            for state in params["states"]:
                query_params.append(f"states={state}")
            query_string = "&".join(query_params)
            full_url = f"{url}?{query_string}"
            
            response = await self._make_request(session, "GET", full_url, headers=headers)
            if response and "studentSubmissions" in response:
                submissions_map[key] = response["studentSubmissions"]
            else:
                submissions_map[key] = []
        
        # 并发执行所有请求
        tasks = [fetch_submissions(item) for item in course_work_items]
        await asyncio.gather(*tasks)
        
        return submissions_map
    
    def _format_due_datetime(self, due_date: Dict[str, int], due_time: Optional[Dict[str, int]] = None) -> tuple:
        """格式化截止日期和时间，将UTC时间转换为UTC+9（日本时间）"""
        if not due_date:
            return None, None

        year = due_date.get("year")
        month = due_date.get("month")  
        day = due_date.get("day")
        
        if not all([year, month, day]):
            return None, None

        # 确保类型安全
        if not isinstance(year, int) or not isinstance(month, int) or not isinstance(day, int):
            return None, None

        # 获取时间信息，默认为23:59
        if due_time:
            hours = due_time.get("hours", 0)
            minutes = due_time.get("minutes", 0)
        else:
            hours = 23
            minutes = 59
        
        # 确保hours和minutes是整数
        if not isinstance(hours, int) or not isinstance(minutes, int):
            hours = 23
            minutes = 59
        
        try:
            # 创建UTC时间的datetime对象
            utc_dt = datetime(year, month, day, hours, minutes, tzinfo=timezone.utc)
            
            # 转换为UTC+9（日本时间）
            jst_offset = timedelta(hours=9)
            jst_dt = utc_dt + jst_offset
            
            # 格式化输出
            date_str = f"{jst_dt.year:04d}-{jst_dt.month:02d}-{jst_dt.day:02d}"
            time_str = f"{jst_dt.hour:02d}:{jst_dt.minute:02d}"

            return date_str, time_str

        except ValueError as e:
            logging.error(f"日期时间格式化错误: {e}")
            return None, None

    def _generate_assignment_url(self, course_id: str, course_work_id: str) -> str:
        """生成课题URL"""
        return f"https://classroom.google.com/c/{course_id}/a/{course_work_id}/details"
    
    async def revoke_user_authorization(self, username: str) -> Dict[str, Any]:
        """撤销用户的OAuth授权"""
        try:
            # 从数据库获取用户令牌
            tokens = await db_manager.get_user_tokens(username)
            if not tokens:
                logging.warning(f"用户 {username} 没有存储的令牌，无需撤销")
                return {
                    "success": True,
                    "message": "用户没有存储的令牌",
                    "already_revoked": True
                }
            
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            
            # 如果有访问令牌，尝试通过Google API撤销
            revoke_success = False
            if access_token:
                revoke_success = await self._revoke_token_from_google(access_token)
            
            # 如果访问令牌撤销失败但有刷新令牌，尝试撤销刷新令牌
            if not revoke_success and refresh_token:
                revoke_success = await self._revoke_token_from_google(refresh_token)
            
            # 无论Google API撤销是否成功，都从数据库中删除令牌
            db_success = await db_manager.revoke_user_tokens(username)
            
            if db_success:
                logging.info(f"用户 {username} 的授权已成功撤销")
                return {
                    "success": True,
                    "message": "授权撤销成功",
                    "google_revoke_success": revoke_success,
                    "database_cleanup_success": True
                }
            else:
                logging.error(f"清理用户 {username} 数据库令牌失败")
                return {
                    "success": False,
                    "message": "数据库令牌清理失败",
                    "google_revoke_success": revoke_success,
                    "database_cleanup_success": False
                }
                
        except Exception as e:
            logging.error(f"撤销用户 {username} 授权时出错: {e}")
            return {
                "success": False,
                "message": f"撤销授权失败: {str(e)}",
                "error": str(e)
            }
    
    async def _revoke_token_from_google(self, token: str) -> bool:
        """通过Google API撤销令牌"""
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://oauth2.googleapis.com/revoke"
                data = {"token": token}
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                
                response = await self._make_request(
                    session, "POST", url,
                    headers=headers, data=urlencode(data)
                )
                
                # Google撤销API成功时返回200状态码，但响应体为空
                # 这里我们通过检查是否没有错误来判断成功
                if response is not None or True:  # Google revoke API返回空响应表示成功
                    logging.info("令牌已通过Google API撤销")
                    return True
                else:
                    logging.warning("Google API令牌撤销可能失败")
                    return False
                    
        except Exception as e:
            logging.error(f"通过Google API撤销令牌时出错: {e}")
            return False
    
    async def get_user_assignments(self, username: str) -> List[Dict[str, Any]]:
        """获取用户的未完成课题"""
        # 获取有效的访问令牌
        access_token = await self._get_valid_access_token(username)
        if not access_token:
            logging.error(f"无法获取用户 {username} 的有效访问令牌")
            return []
        
        async with aiohttp.ClientSession() as session:
            try:
                # 1. 获取所有活跃课程
                courses = await self._get_active_courses(session, access_token)
                if not courses:
                    logging.info(f"用户 {username} 没有活跃课程")
                    return []
                
                logging.info(f"用户 {username} 有 {len(courses)} 个活跃课程")
                
                # 创建课程ID到课程名称的映射
                course_name_map = {course["id"]: course["name"] for course in courses}
                course_ids = list(course_name_map.keys())
                
                # 2. 批处理获取所有课程的课题
                course_work_map = await self._get_course_work_batch(session, access_token, course_ids)
                
                # 3. 筛选有截止时间的课题，并且去除已经超过截止时间1天以上的课题
                course_work_with_due = []
                # 获取当前UTC时间减去1天作为阈值
                one_day_ago_utc = datetime.now(timezone.utc) - timedelta(days=1)
                
                for course_id, course_work_list in course_work_map.items():
                    for work in course_work_list:
                        if "dueDate" in work:  # 只处理有截止时间的课题
                            due_date_data = work['dueDate']
                            due_time_data = work.get('dueTime')
                            
                            # 获取时间信息，如果没有指定时间，默认为23:59（与_format_due_datetime保持一致）
                            if due_time_data:
                                hours = due_time_data.get('hours', 0)
                                minutes = due_time_data.get('minutes', 0)
                            else:
                                hours = 23
                                minutes = 59
                            
                            # 构建UTC时间的datetime对象
                            due_date_utc = datetime(
                                due_date_data['year'],
                                due_date_data['month'],
                                due_date_data['day'],
                                hours,
                                minutes,
                                tzinfo=timezone.utc
                            )
                            
                            # 只保留未超过1天的课题（即截止时间在昨天之后的课题）
                            if due_date_utc >= one_day_ago_utc:
                                course_work_with_due.append(work)
                
                if not course_work_with_due:
                    logging.info(f"用户 {username} 没有有截止时间的课题")
                    return []
                
                logging.info(f"用户 {username} 有 {len(course_work_with_due)} 个有截止时间的课题")
                
                # 4. 批处理获取课题的学生提交状态
                submissions_map = await self._get_student_submissions_batch(
                    session, access_token, course_work_with_due
                )
                
                # 5. 汇总结果
                pending_assignments = []
                for work in course_work_with_due:
                    course_id = work["courseId"]
                    course_work_id = work["id"]
                    key = f"{course_id}_{course_work_id}"
                    
                    # 检查是否有未完成的提交
                    submissions = submissions_map.get(key, [])
                    if submissions:  # 有NEW或CREATED状态的提交，说明未完成
                        due_date, due_time = self._format_due_datetime(
                            work.get("dueDate"), 
                            work.get("dueTime")
                        )
                        if due_date:  # 确保有有效的截止日期
                            assignment = {
                                "title": work.get("title", "未命名课题"),
                                "courseId": course_id,
                                "courseName": course_name_map.get(course_id, "未知课程"),
                                "dueDate": due_date,
                                "dueTime": due_time,
                                "description": work.get("description", ""),
                                "url": work.get("alternateLink", self._generate_assignment_url(course_id, course_work_id))
                            }
                            pending_assignments.append(assignment)
                
                logging.info(f"用户 {username} 有 {len(pending_assignments)} 个未完成的课题")
                return pending_assignments
                
            except Exception as e:
                logging.error(f"获取用户 {username} 课题时出错: {e}")
                return []


# 全局Google Classroom API实例
classroom_api = GoogleClassroomAPI()
