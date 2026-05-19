import time
from Agents.IntentAgent.intent_agent import classify_intent

prompts = {

}




def mian():
    correct_count = 0
    

    start_time = time.time()
    for prompt, intent in prompts.item():
        res = classify_intent(prompt)
        if intent == res['intent']:
            correct_count += 1

    end_time  = time.time()

    print()


