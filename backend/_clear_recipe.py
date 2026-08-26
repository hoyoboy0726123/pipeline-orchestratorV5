import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
wid = open("_demo_wid.txt").read().strip()
db.delete_workflow_recipes(wid)
print("recipes cleared", wid)
