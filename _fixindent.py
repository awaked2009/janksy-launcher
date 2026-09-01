import sys

with open('janksy.py', 'rb') as f:
    lines = f.readlines()

# Fix line 1613 indentation - should be 8 spaces (method body level)
old_1613 = lines[1612]
# Normalize to 8 spaces
new_1613 = b'        self.loader_var = ctk.StringVar(value="All Loaders")\r\n'
lines[1612] = new_1613

with open('janksy.py', 'wb') as f:
    f.writelines(lines)

print('FIXED line 1613')
# Verify
for i in range(1610, 1625):
    print(i+1, repr(lines[i][:80]))