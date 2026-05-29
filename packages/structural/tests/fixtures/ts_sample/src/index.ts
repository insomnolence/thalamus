import { Circle } from "./circle";

// A top-level function calling a constructor cross-file — exercises a `calls`
// edge whose callee is a constructor/method on a class from another module.
export function makeCircle(radius: number): Circle {
  return new Circle(radius);
}
