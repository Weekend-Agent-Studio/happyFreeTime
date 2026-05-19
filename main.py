"""周末闲时活动规划助手 —— 入口"""
from Agents.intent_agent import classify_intent

TEST_CASES = [
    "帮我规划一下这周末的活动",
    "周六下午有什么好的户外运动推荐？",
    "周末天气怎么样，适合出去玩吗？",
    "把周日的爬山换成看电影吧",
    "你好啊，今天过得怎么样？",
    "推荐一个适合情侣约会的餐厅",
    "下周想去爬山，帮我看看天气",
    "今天下午是空的，想和老婆孩子/朋友出去玩几个小时，别离家太远，帮我安排一下。"
]


def main():
    for i, text in enumerate(TEST_CASES, 1):
        result = classify_intent(text)
        print(f"\n{'=' * 55}")
        print(f"[{i}] 用户: {text}")
        print(f"    意图    : {result['intent']}")
        print(f"    置信度  : {result['confidence']}")
        print(f"    槽位    : {result['slots']}")
        print(f"    理由    : {result['reasoning']}")


if __name__ == "__main__":
    main()
