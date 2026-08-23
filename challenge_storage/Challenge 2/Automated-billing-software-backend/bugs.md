# Implanted Logical Bugs

This document details the 15 logical bugs intentionally implanted in `main_code.py` for the debugging challenge. None of these will produce immediate syntax errors, but they break the functionality of the software.

1. **Admin Login Bug (Lines 17-18)**
   - **Bug:** `result = m1.check_admin(pas,uid)`
   - **Effect:** Reverses the user ID and password arguments. The admin won't be able to log in with correct credentials unless they enter them in reverse order.

2. **Add Employee Bug (Lines 33-35)**
   - **Bug:** `m1.add_employee(new_pass,new_user)`
   - **Effect:** Reverses the username and password arguments when adding an employee, storing the ID as the password and vice versa.

3. **Update Data Bug (Lines 51-53)**
   - **Bug:** `m1.update_data(price, name)`
   - **Effect:** Passes the price as the name and the name as the price, which will either cause an error in `m1` or silently fail/corrupt data.

4. **Admin Exit Bug (Lines 70-73)**
   - **Bug:** `continue` instead of `break`
   - **Effect:** The admin is trapped in the `while` loop when choosing 'Exit' (Option 6).

5. **Employee Login Bypass Bug (Lines 89-91)**
   - **Bug:** `if result == "verified" or True:`
   - **Effect:** Bypasses the login verification entirely, allowing any input to successfully log in as an employee.

6. **Customer Cart Item Condition Bug (Lines 188-191)**
   - **Bug:** `if num != -1:`
   - **Effect:** Makes it impossible to add multiple items normally. The customer breaks out of the loop immediately unless they enter `-1`, which defeats the purpose of `-1` as the break condition.

7. **Customer Cart Tuple Structure Bug (Lines 196-202)**
   - **Bug:** `cart.append((item_name, num, item_price))`
   - **Effect:** Messes up the tuple structure which later expects `num` at index 0 and `item_name` at index 1.

8. **Customer Cart Total Bug (Lines 196-202)**
   - **Bug:** `total_price -= item_price`
   - **Effect:** Subtracts the price of the item from the total instead of adding to it.

9. **Customer Empty Cart Check Bug (Lines 220-221)**
   - **Bug:** `if cart :` instead of `if not cart :`
   - **Effect:** Reverses the logic: it claims the cart is empty when it has items, and tries to print items when the cart is actually empty.

10. **Billing Tax Bug (Line 251)**
    - **Bug:** `tax = total_price / 0.18`
    - **Effect:** Inflates the tax astronomically instead of multiplying by 0.18 (18%).

11. **Billing Discount Bug (Lines 252-253)**
    - **Bug:** `if total_price < 1000:`
    - **Effect:** Gives a discount for purchases under 1000 instead of over 1000.

12. **Billing Final Price Bug (Line 258)**
    - **Bug:** `print(f"Final prize : ₹{total_price + discount - tax}")`
    - **Effect:** Reverses the math: adds the discount and subtracts the tax instead of the other way around.

13. **Billing Cancel Purchase Bug (Lines 266-267)**
    - **Bug:** Cart is reset regardless of purchase confirmation.
    - **Effect:** Moved `cart = []` and `total_price = 0` outside the `if confirm` block, so even if the customer cancels the purchase, they lose their cart.

14. **Main Menu Admin/Employee Swap Bug (Lines 302-314)**
    - **Bug:** `case 1:` calls `employee()` instead of `admin()`.
    - **Effect:** If the user selects Admin on the main menu, they are shown the Employee login/flow instead.

15. **Main Menu Customer/Admin Swap Bug (Lines 302-314)**
    - **Bug:** `case 3:` calls `admin()` instead of `customer()`.
    - **Effect:** If the user selects Customer on the main menu, they are shown the Admin login/flow instead.
