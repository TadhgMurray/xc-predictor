import corrections as c
for n in ['_RESULT_DROP_TF','_RESULT_DROP_XC','_DISTANCE_OVERRIDES_XC','_DISTANCE_DROP_TF','_RESULT_OVERRIDE_TF']:
    print(n, len(getattr(c, n)))
