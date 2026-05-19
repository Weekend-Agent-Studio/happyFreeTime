from datetime import datetime
from langchain_core.tools import tool

@tool
def get_cur_time():
    '''
    这个工具用于获取当前的时间
    '''
    now = datetime.now()

    week_list = [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日"
    ]

    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": week_list[now.weekday()],
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second
    }

@tool
def get_cur_loc():
    '''
    获取当前地理位置。当前为 Demo 版本，返回默认地址。
    '''
    return {
        "address": "北京市朝阳区建国路88号",
        "lat": 39.9087,
        "lng": 116.4713,
        "district": "朝阳区",
        "nearby": ["国贸", "双井", "大望路"]
    }

@tool
def get_cur_weather(loc, date):
    '''
    根据传入的位置和日期获取天气情况。当前为 Demo 版本，返回默认内容。
    '''
    return {
        "address": loc["address"],
        "district": loc["district"],
        "date": date,
        "weather": "晴朗",
        "temperature": "12摄氏度",
        "broadcast": "未来24小时会有降水"
    }

@tool
def search_restaurants(loc, preferences, party_size):
    '''
    根据位置、偏好、人数搜索餐厅。Demo 版返回 Mock 数据。
    '''

    return [
        {
            "name": "海底捞",
            "rating": 4.8,
            "cuisine": "火锅",
            "address": "北京市朝阳区xxx",
            "phone": "123456789",
            "online_booking": True,
            "avg_price": 120,
            "top3_dishes": ["毛肚", "虾滑", "肥牛"],
            "distance": 3,
            "highchair": True,
            "opening_hours": "11:00-22:00"
        },

        {
            "name": "巴奴火锅",
            "rating": 4.7,
            "cuisine": "火锅",
            "address": "北京市海淀区xxx",
            "phone": "987654321",
            "online_booking": False,
            "avg_price": 150,
            "top3_dishes": ["毛肚", "鸭血", "和牛"],
            "distance": 5,
            "highchair": False,
            "opening_hours": "10:00-23:00"
        },
        {
            "name": "西贝莜面村",
            "rating": 4.6,
            "cuisine": "西北菜",
            "address": "北京市丰台区xxx",
            "phone": "135792468",
            "online_booking": True,
            "avg_price": 95,
            "top3_dishes": ["烤羊排", "莜面鱼鱼", "黄米凉糕"],
            "distance": 2,
            "highchair": True,
            "opening_hours": "09:30-21:30"
        }
    ]

@tool
def search_activities(loc, preferences, party_size):
    '''
    根据位置、偏好、人数搜索活动。Demo 版返回 Mock 数据。
    '''

    return [
        {
            "name": "亲子乐园",
            "category": "entertainment",
            "type": "indoor",
            "rating": 4.8,
            "address": "北京市朝阳区xxx",
            "contact_phone": "123456789",
            "online_booking": True,
            "avg_price": 120,
            "distance_km": 3,
            "duration_hours": 3,
            "opening_hours": "11:00-22:00",
            "suitable_for": ["亲子", "3-12岁"],
            "weather_sensitive": False
        },
        {
            "name": "儿童科学馆",
            "category": "education",
            "type": "indoor",
            "rating": 4.7,
            "address": "北京市海淀区xxx",
            "contact_phone": "987654321",
            "online_booking": True,
            "avg_price": 80,
            "distance_km": 5,
            "duration_hours": 2,
            "opening_hours": "09:00-18:00",
            "suitable_for": ["亲子", "5-15岁"],
            "weather_sensitive": False
        },
        {
            "name": "公园骑行",
            "category": "sports",
            "type": "outdoor",
            "rating": 4.6,
            "location": "奥林匹克森林公园",
            "contact_phone": None,
            "online_booking": False,
            "avg_price": 0,
            "distance_km": 4,
            "duration_hours": 2,
            "opening_hours": "全天",
            "suitable_for": ["亲子", "朋友"],
            "weather_sensitive": True
        },
        {
            "name": "香山徒步",
            "category": "sports",
            "type": "outdoor",
            "rating": 4.7,
            "location": "香山公园",
            "contact_phone": None,
            "online_booking": False,
            "avg_price": 20,
            "distance_km": 12,
            "duration_hours": 4,
            "opening_hours": "全天",
            "suitable_for": ["朋友", "运动爱好者"],
            "weather_sensitive": True
        }
    ]

@tool
def search_extras(loc, preferences):
    '''
    搜索增量消费：甜品、鲜花、蛋糕、小吃街等。Demo 版返回 Mock 数据。
    '''

    return [
        {
            "name": "幸福西饼",
            "category": "蛋糕",
            "rating": 4.6,
            "address": "北京市朝阳区建外大街xxx",
            "contact_phone": "400-123-4567",
            "online_order": True,
            "avg_price": 200,
            "distance_km": 2,
            "opening_hours": "08:00-21:00",
            "delivery_available": True
        },
        {
            "name": "花点时间（国贸店）",
            "category": "鲜花",
            "rating": 4.8,
            "address": "北京市朝阳区国贸商城xxx",
            "contact_phone": "400-987-6543",
            "online_order": True,
            "avg_price": 150,
            "distance_km": 1.5,
            "opening_hours": "09:00-20:00",
            "delivery_available": True
        },
        {
            "name": "满记甜品",
            "category": "甜品",
            "rating": 4.5,
            "address": "北京市朝阳区双井xxx",
            "contact_phone": "010-1234-5678",
            "online_order": False,
            "avg_price": 40,
            "distance_km": 2.5,
            "opening_hours": "10:00-22:00",
            "delivery_available": False
        },
        {
            "name": "南锣鼓巷小吃街",
            "category": "小吃街",
            "rating": 4.4,
            "address": "北京市东城区南锣鼓巷",
            "contact_phone": None,
            "online_order": False,
            "avg_price": 50,
            "distance_km": 6,
            "opening_hours": "全天",
            "delivery_available": False
        }
    ]


@tool
def reserve_restaurant(name, address, time, party_size, highchair):
    '''
    预订餐厅座位。Demo 版返回 Mock 结果。
    '''

    return {
        "is_success": True,
        "order_number": "R20260518-0032",
        "name": name,
        "address": address,
        "time": time,
        "party_size": party_size,
        "highchair": highchair
    }


@tool
def book_tickets(name, address, count, date, time_slot):
    '''
    预订活动门票。Demo 版返回 Mock 结果。
    '''

    return {
        "is_success": True,
        "ticket_code": "TK20260518-0097",
        "name": name,
        "address": address,
        "count": count,
        "date": date,
        "time_slot": time_slot
    }


@tool
def place_order(item_name, delivery_address, delivery_time, quantity):
    '''
    下单商品（鲜花、蛋糕等）并配送到指定地址。Demo 版返回 Mock 结果。
    '''

    return {
        "is_success": True,
        "order_id": "ORD20260518-0145",
        "item_name": item_name,
        "delivery_address": delivery_address,
        "delivery_time": delivery_time,
        "quantity": quantity
    }

